# Copyright (C) 2015 GRNET S.A.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from functools import wraps
import time
import os
import datetime
import threading
import logging
import re

from agkyra.syncer import utils, common, messaging
from agkyra.syncer.file_client import FileClient
from agkyra.syncer.setup import ClientError
from agkyra.syncer.database import transaction

logger = logging.getLogger(__name__)


def heartbeat_event(settings, heartbeat, objname):
    event = threading.Event()
    max_interval = settings.action_max_wait / 2.0

    def set_log():
        with heartbeat.lock() as hb:
            tstamp = utils.time_stamp()
            hb.set(objname, tstamp)
            logger.debug("HEARTBEAT '%s' %s" % (objname, tstamp))

    def go():
        interval = 0.2
        while True:
            if event.is_set():
                break
            set_log()
            time.sleep(interval)
            interval = min(1.2 * interval, max_interval)
    thread = threading.Thread(target=go)
    thread.start()
    return event


def give_heartbeat(f):
    @wraps(f)
    def inner(*args, **kwargs):
        obj = args[0]
        objname = obj.objname
        heartbeat = obj.heartbeat
        settings = obj.settings
        event = heartbeat_event(settings, heartbeat, objname)
        try:
            return f(*args, **kwargs)
        finally:
            event.set()
    return inner


def handle_client_errors(f):
    @wraps(f)
    def inner(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except ClientError as e:
            if e.status == 412:  # Precondition failed
                raise common.CollisionError(e)
            # TODO handle other cases, too
            raise common.SyncError(e)
    return inner


class PithosSourceHandle(object):
    def __init__(self, settings, source_state):
        self.SIGNATURE = "PithosSourceHandle"
        self.settings = settings
        self.endpoint = settings.endpoint
        self.cache_fetch_name = settings.cache_fetch_name
        self.cache_fetch_path = settings.cache_fetch_path
        self.cache_path = settings.cache_path
        self.get_db = settings.get_db
        self.source_state = source_state
        self.objname = source_state.objname
        self.heartbeat = settings.heartbeat

    @transaction()
    def register_fetch_name(self, filename):
        db = self.get_db()
        f = utils.hash_string(filename) + "_" + \
            datetime.datetime.now().strftime("%s")
        fetch_name = utils.join_path(self.cache_fetch_name, f)
        self.fetch_name = fetch_name
        db.insert_cachename(fetch_name, self.SIGNATURE, filename)
        return utils.join_path(self.cache_path, fetch_name)

    @handle_client_errors
    @give_heartbeat
    def send_file(self, sync_state):
        fetched_fspath = self.register_fetch_name(self.objname)
        headers = dict()
        with open(fetched_fspath, mode='wb+') as fil:
            try:
                logger.info("Downloading object: '%s', to: '%s'" %
                            (self.objname, fetched_fspath))
                self.endpoint.download_object(
                    self.objname,
                    fil,
                    headers=headers)
            except ClientError as e:
                if e.status == 404:
                    actual_info = {}
                else:
                    raise e
            else:
                actual_etag = headers["x-object-hash"]
                actual_type = (common.T_DIR if object_isdir(headers)
                               else common.T_FILE)
                actual_info = {"pithos_etag": actual_etag,
                               "pithos_type": actual_type}
            self.source_state = self.source_state.set(info=actual_info)
        if actual_info == {}:
            logger.info("Downloading object: '%s', object is gone."
                        % self.objname)
            os.unlink(fetched_fspath)
        elif actual_info["pithos_type"] == common.T_DIR:
            logger.info("Downloading object: '%s', object is dir."
                        % self.objname)
            os.unlink(fetched_fspath)
            os.mkdir(fetched_fspath)
        return fetched_fspath

    def get_synced_state(self):
        return self.source_state

    def unstage_file(self):
        pass

STAGED_FOR_DELETION_SUFFIX = ".pithos_staged_for_deletion"
exclude_staged_regex = ".*" + STAGED_FOR_DELETION_SUFFIX + "$"
exclude_pattern = re.compile(exclude_staged_regex)


class PithosTargetHandle(object):
    def __init__(self, settings, target_state):
        self.settings = settings
        self.endpoint = settings.endpoint
        self.target_state = target_state
        self.target_objname = target_state.objname
        self.objname = target_state.objname
        self.heartbeat = settings.heartbeat

    def mk_del_name(self, name, etag):
        return "%s.%s%s" % (name, etag, STAGED_FOR_DELETION_SUFFIX)

    def safe_object_del(self, objname, etag):
        container = self.endpoint.container
        del_name = self.mk_del_name(objname, etag)
        logger.info("Moving temporarily to '%s'" % del_name)
        try:
            self.endpoint.object_move(
                objname,
                destination='/%s/%s' % (container, del_name),
                if_etag_match=etag)
        except ClientError as e:
            if e.status == 404:
                logger.warning("'%s' not found; already moved?" % objname)
            else:
                raise
        finally:
            self.endpoint.del_object(del_name)
            logger.info("Deleted tmp '%s'" % del_name)

    def directory_put(self, objname, etag):
        r = self.endpoint.object_put(
            objname,
            content_type='application/directory',
            content_length=0,
            if_etag_match=etag)
        return r

    @handle_client_errors
    @give_heartbeat
    def pull(self, source_handle, sync_state):
#        assert isinstance(source_handle, LocalfsSourceHandle)
        info = sync_state.info
        etag = info.get("pithos_etag")
        try:
            if source_handle.info_is_deleted_or_unhandled():
                if etag is not None:
                    logger.info("Deleting object '%s'" % self.target_objname)
                    self.safe_object_del(self.target_objname, etag)
                live_info = {}
            elif source_handle.info_is_dir():
                logger.info("Creating dir '%s'" % self.target_objname)
                r = self.directory_put(self.target_objname, etag)
                synced_etag = r.headers["etag"]
                live_info = {"pithos_etag": synced_etag,
                             "pithos_type": common.T_DIR}
            else:
                with open(source_handle.staged_path, mode="rb") as fil:
                    r = self.endpoint.upload_object(
                        self.target_objname,
                        fil,
                        if_etag_match=info.get("pithos_etag"))
                    synced_etag = r["etag"]
                live_info = {"pithos_etag": synced_etag,
                             "pithos_type": common.T_FILE}
            return self.target_state.set(info=live_info)
        except ClientError as e:
            if e.status == 412:  # Precondition failed
                msg = messaging.CollisionMessage(
                    objname=self.target_objname, etag=etag, logger=logger)
                self.settings.messager.put(msg)
                raise common.CollisionError(e)
            else:
                raise


def object_isdir(obj):
    try:
        content_type = obj["content_type"]
    except KeyError:
        content_type = obj["content-type"]
    return any(txt in content_type for txt in ['application/directory',
                                               'application/folder'])


PITHOS_TYPE = "pithos_type"
PITHOS_ETAG = "pithos_etag"


class PithosFileClient(FileClient):
    def __init__(self, settings):
        self.settings = settings
        self.SIGNATURE = "PithosFileClient"
        self.auth_url = settings.auth_url
        self.auth_token = settings.auth_token
        self.container = settings.container
        self.get_db = settings.get_db
        self.endpoint = settings.endpoint
        self.last_modification = "0000-00-00"
        self.probe_candidates = common.LockedDict()

    def list_candidate_files(self, forced=False):
        if forced:
            candidates = self.get_pithos_candidates()
            self.probe_candidates.update(candidates)
        return self.probe_candidates.keys()

    def get_pithos_candidates(self, last_modified=None):
        db = self.get_db()
        objects = self.endpoint.list_objects()
        self.objects = objects
        upstream_all = {}
        for obj in objects:
            name = obj["name"]
            upstream_all[name] = self.get_object_live_info(obj)
            obj_last_modified = obj["last_modified"]
            if obj_last_modified > self.last_modification:
                self.last_modification = obj_last_modified
        upstream_all_names = set(upstream_all.keys())
        if last_modified is not None:
            upstream_modified = {}
            for obj in objects:
                name = obj["name"]
                if obj["last_modified"] > last_modified:
                    upstream_modified[name] = upstream_all[name]
            candidates = upstream_modified
        else:
            candidates = upstream_all

        non_deleted_in_db = set(db.list_non_deleted_files(self.SIGNATURE))
        newly_deleted_names = non_deleted_in_db.difference(upstream_all_names)
        logger.debug("newly_deleted %s" % newly_deleted_names)
        newly_deleted = dict((name, {}) for name in newly_deleted_names)

        candidates.update(newly_deleted)
        logger.info("Candidates since %s: %s" %
                    (last_modified, candidates))
        return candidates

    def notifier(self, callback=None, interval=2):
        class PollPithosThread(utils.StoppableThread):
            def run_body(this):
                candidates = self.get_pithos_candidates(
                    last_modified=self.last_modification)
                self.probe_candidates.update(candidates)
                time.sleep(interval)
        return utils.start_daemon(PollPithosThread)

    def get_object(self, objname):
        try:
            return self.endpoint.get_object_info(objname)
        except ClientError as e:
            if e.status == 404:
                return None
            raise e

    def get_object_live_info(self, obj):
        if obj is None:
            return {}
        p_type = common.T_DIR if object_isdir(obj) else common.T_FILE
        obj_hash = obj.get("x-object-hash")
        if obj_hash is None:
            obj_hash = obj.get("x_object_hash")
        return {PITHOS_ETAG: obj_hash,
                PITHOS_TYPE: p_type,
                }

    def start_probing_file(self, objname, old_state, ref_state,
                           assumed_info=None,
                           callback=None):
        if exclude_pattern.match(objname):
            logger.warning("Ignoring probe archive: %s, object: '%s'" %
                           (old_state.archive, objname))
            return
        info = old_state.info
        cached_info = self.probe_candidates.pop(objname)
        if cached_info is None:
            obj = self.get_object(objname)
            live_info = self.get_object_live_info(obj)
        else:
            live_info = cached_info
        if info != live_info:
            if callback is not None:
                live_state = old_state.set(info=live_info)
                callback(live_state)

    def stage_file(self, source_state):
        return PithosSourceHandle(self.settings, source_state)

    def prepare_target(self, target_state):
        return PithosTargetHandle(self.settings, target_state)
