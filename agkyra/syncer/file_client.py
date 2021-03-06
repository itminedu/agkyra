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

import logging
logger = logging.getLogger(__name__)

from agkyra.syncer import common, messaging


class FileClient(object):

    def list_candidate_files(self, archive):
        raise NotImplementedError

    def start_probing_file(self, objname, old_state, ref_state, callback=None):
        raise NotImplementedError

    def stage_file(self, source_state):
        raise NotImplementedError

    def prepare_target(self, state):
        raise NotImplementedError

    def start_pulling_file(self, source_handle, target_state, sync_state,
                           callback=None):
        synced_source_state, synced_target_state = \
            self._start(source_handle, target_state, sync_state)
        if callback is not None:
            callback(synced_source_state, synced_target_state)

    def _start(self, source_handle, target_state, sync_state):
        try:
            target_handle = self.prepare_target(target_state)
            synced_target_state = target_handle.pull(source_handle, sync_state)
            synced_source_state = source_handle.get_synced_state()
            return synced_source_state, synced_target_state
        finally:
            source_handle.unstage_file()
