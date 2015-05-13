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

import cmd
import sys
import logging
from agkyra.syncer import setup, syncer
from agkyra.syncer.pithos_client import PithosFileClient
from agkyra.syncer.localfs_client import LocalfsFileClient
from agkyra import config


LOG = logging.getLogger(__name__)


class AgkyraCLI(cmd.Cmd):
    """The CLI for """

    cnf = config.AgkyraConfig()
    is_shell = False

    def init(self):
        """initialize syncer"""
        # Read settings
        sync = self.cnf.get('global', 'default_sync')
        LOG.info('Using sync: %s' % sync)
        cloud = self.cnf.get_sync(sync, 'cloud')
        url = self.cnf.get_cloud(cloud, 'url')
        token = self.cnf.get_cloud(cloud, 'token')
        container = self.cnf.get_sync(sync, 'container')
        directory = self.cnf.get_sync(sync, 'directory')

        # Prepare syncer settings
        self.settings = setup.SyncerSettings(
            sync, url, token, container, directory,
            ignore_ssl=True)
        LOG.info('Local: %s' % directory)
        LOG.info('Remote: %s of %s' % (container, url))
        # self.exclude = self.cnf.get_sync(sync, 'exclude')

        # Init syncer
        master = PithosFileClient(self.settings)
        slave = LocalfsFileClient(self.settings)
        self.syncer = syncer.FileSyncer(self.settings, master, slave)

    def preloop(self):
        """This runs when the shell loads"""
        print 'Loading Agkyra (sometimes this takes a while)'
        if not self.is_shell:
            self.is_shell = True
            self.prompt = '\xe2\x9a\x93 '
            self.init()
        self.default('')

    def print_option(self, section, name, option):
        """Print a configuration option"""
        section = '%s.%s' % (section, name) if name else section
        value = self.cnf.get(section, option)
        print '  %s: %s' % (option, value)

    def list_section(self, section, name):
        """list contents of a section"""
        content = dict(self.cnf.items(section))
        if section in 'global' and name:
            self.print_option(section, '', name)
        else:
            if name:
                content = content[name]
            for option in content.keys():
                self.print_option(section, name, option)

    def list_section_type(self, section):
        """print the contents of a configuration section"""
        names = ['', ] if section in ('global', ) else self.cnf.keys(section)
        assert names, 'Section %s not found' % section
        for name in names:
            print section, name
            self.list_section(section, name)

    def list_sections(self):
        """List all configuration sections"""
        for section in self.cnf.sections():
            self.list_section_type(section)

    def do_list(self, line):
        """List current settings (\"help list\" for details)
        list global                 List all settings
        list global <option>        Get the value of this global option
        list cloud                  List all clouds
        list cloud <name>           List all options of a cloud
        list cloud <name> <option>  Get the value of this cloud option
        list sync                   List all syncs
        list sync <name>            List all options of a sync
        list sync <name> <option>   Get the value of this sync option
        """
        args = line.split()
        try:
            {
                0: self.list_sections,
                1: self.list_section_type,
                2: self.list_section,
                3: self.print_option
            }[len(args)](*args)
        except Exception as e:
            sys.stderr.write('%s\n' % e)
            cmd.Cmd.do_help(self, 'list')

    def set_global_setting(self, section, option, value):
        assert section in ('global'), 'Syntax error'
        self.cnf.set(section, option, value)

    def set_setting(self, section, name, option, value):
        assert section in self.sections(), 'Syntax error'
        self.cnf.set('%s.%s' % (section, name), option, value)

    def do_set(self, line):
        """Set a setting"""
        args = line.split()
        try:
            {
                3: self.set_global_setting,
                4: self.set_setting
            }[len(args)](*args)
            self.cnf.write()
        except Exception as e:
            sys.stderr.write('%s\n' % e)
            cmd.Cmd.do_help(self, 'set')

    def do_start(self, line):
        """Start syncing"""
        if not getattr(self, '_syncer_initialized', False):
            self.syncer.probe_and_sync_all()
            self._syncer_initialized = True
        if self.syncer.paused:
            self.syncer.start_decide()

    def do_pause(self, line):
        """Pause syncing"""
        if not self.syncer.paused:
            self.syncer.pause_decide()

    def do_status(self, line):
        """Get current status (running/paused, progress)"""
        print 'paused' if self.syncer.paused else 'running'

    # def do_shell(self, line):
    #     """Run system, shell commands"""
    #     if getattr(self, 'is_shell'):
    #         os.system(line)
    #     else:
    #         try:
    #             self.prompt = '\xe2\x9a\x93 '
    #             self.is_shell = True
    #         finally:
    #             self.init()
    #             self.cmdloop()

    def do_help(self, line):
        """List commands with \"help\" or detailed help with \"help cmd\""""
        if not line:
            self.default(line)
        cmd.Cmd.do_help(self, line)

    def do_quit(self, line):
        """Quit Agkyra shell"""
        return True

    def default(self, line):
        """print help"""
        sys.stderr.write('Usage:\t%s <command> [args]\n\n' % self.prompt)
        for arg in [c for c in self.get_names() if c.startswith('do_')]:
            sys.stderr.write('%s\t' % arg[3:])
            method = getattr(self, arg)
            sys.stderr.write(method.__doc__.split('\n')[0] + '\n')
        sys.stderr.write('\n')

    def emptyline(self):
        if not self.is_shell:
            return self.default('')

    def run_onecmd(self, argv):
        self.prompt = argv[0]
        self.init()
        self.onecmd(' '.join(argv[1:]))


# AgkyraCLI().run_onecmd(sys.argv)

# or run a shell with
# AgkyraCLI().cmdloop()