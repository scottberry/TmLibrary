# TmLibrary - TissueMAPS library for distibuted image analysis routines.
# Copyright (C) 2016  Markus D. Herrmann, University of Zurich and Robin Hafen
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
import os
from abc import ABCMeta
import logging
from ConfigParser import SafeConfigParser
from ConfigParser import NoOptionError
from gc3libs.config import Configuration

logger = logging.getLogger(__name__)


CONFIG_FILE = os.path.expanduser('~/.tmaps/tissuemaps.cfg')
DEFAULT_LIB = 'pandas'
IMPLEMENTED_LIBS = {DEFAULT_LIB, 'spark'}


class TmapsConfig(object):

    '''Abstract base class for `TissueMAPS` configuration settings.

    `TissueMAPS` code is distributed across mutliple Python packages,
    but configuration settings are bundeled in one global config file
    (:attr:`CONFIG_FILE <tmlib.config.CONFIG_FILE>`)
    with an `INI <https://en.wikipedia.org/wiki/INI_file>`_-like file format.

    The environment variable ``TMAPS_CONFIG_FILE`` can be used to overwrite
    the default location of the file.

    Properties defined on this base class are written into the ``DEFAULT``
    section of the file. Each package that requires configuration should
    implement this base class and create a separate package-specific
    section in the config file for the derived class.
    '''

    __meta__ = ABCMeta

    __slots__ = ('_config_file', '_config', '_section')

    def __init__(self):
        if 'TMAPS_CONFIG_FILE' in os.environ:
            self._config_file = os.environ['TMAPS_CONFIG_FILE']
            logger.info(
                'use config file set by environment variable TMAPS_CONFIG_FILE'
            )
        else:
            self._config_file = CONFIG_FILE
            logger.info('use default config file')
        logger.debug('config file: %s', self._config_file)
        if not os.path.exists(self._config_file):
            logger.warn(
                'configuration file does not exist: %s' % self._config_file
            )
        self._config = SafeConfigParser()
        self._section = self.__class__.__module__.split('.')[0]
        if not self._config.has_section(self._section):
            self._config.add_section(self._section)
        self.db_user = 'tissuemaps'
        self.db_host = 'localhost'
        self.db_port = 5432
        self.db_nodes = 2

    def read(self):
        '''Reads the configuration from file.

        See Also
        --------
        :const:`tmlib.config.CONFIG_FILE`
        '''
        logger.debug('read config file: "%s"', self._config_file)
        try:
            self._config.read(self._config_file)
        except OSError:
            logger.warn('no configuration file found')

    def write(self):
        '''Writes the configuration to file.'''
        with open(self._config_file, 'wb') as f:
            self._config.write(f)

    @property
    def db_user(self):
        '''str: database user (default: ``"tissuemaps"``)'''
        return self._config.get('DEFAULT', 'db_user')

    @db_user.setter
    def db_user(self, value):
        if not isinstance(value, basestring):
            raise ValueError(
                'Configuration parameter "db_user" must have type str.'
            )
        self._config.set('DEFAULT', 'db_user', str(value))

    @property
    def db_password(self):
        '''str: database password

        Note
        ----
        Must be an alphanumeric string without special characters.
        '''
        try:
            # Workaround special characters like %
            return self._config.get('DEFAULT', 'db_password')
        except NoOptionError:
            return ''

    @db_password.setter
    def db_password(self, value):
        if not isinstance(value, basestring):
            raise ValueError(
                'Configuration parameter "db_password" must have type str.'
            )
        if not value.isalnum():
            raise ValueError(
                'Argument "db_password" must be alphanumeric.'
            )
        self._config.set('DEFAULT', 'db_password', value)

    @property
    def db_host(self):
        '''str: IP address or DNS name of master database
        (default: ``"localhost"``)
        '''
        return self._config.get('DEFAULT', 'db_host')

    @db_host.setter
    def db_host(self, value):
        if not isinstance(value, basestring):
            raise ValueError(
                'Configuration parameter "db_host" must have type str.'
            )
        self._config.set('DEFAULT', 'db_host', value)

    @property
    def db_port(self):
        '''str: port of the master database (default: ``5432``)'''
        return self._config.getint('DEFAULT', 'db_port')

    @db_port.setter
    def db_port(self, value):
        if not isinstance(value, int):
            raise ValueError(
                'Configuration parameter "db_port" must have type int.'
            )
        self._config.set('DEFAULT', 'db_port', str(value))

    @property
    def db_nodes(self):
        '''int: number of database worker nodes (default: ``2``)'''
        return self._config.getint('DEFAULT', 'db_nodes')

    @db_nodes.setter
    def db_nodes(self, value):
        if not isinstance(value, int):
            raise ValueError(
                'Configuration parameter "db_nodes" must have type int.'
            )
        self._config.set('DEFAULT', 'db_nodes', str(value))

    @staticmethod
    def _get_database_name(experiment_id=None):
        database = 'tissuemaps'
        if experiment_id is not None:
            database += '_experiment_%d' % experiment_id
        return database

    @property
    def db_master_uri(self):
        '''str: URI for the "master" database server'''
        if self.db_password:
            return 'postgresql://{user}:{pw}@{host}:{port}/tissuemaps'.format(
                user=self.db_user, pw=self.db_password,
                host=self.db_host, port=self.db_port,
            )
        else:
            return 'postgresql://{user}@{host}:{port}/tissuemaps'.format(
                user=self.db_user, host=self.db_host, port=self.db_port,
            )

    def build_db_worker_uri(self, host, port):
        '''Builds URI for a "worker" database server.

        Parameters
        ----------
        host: str
            IP address or domain name of the server
        port: int
            port to which the server listens

        Returns
        -------
        str
            URI for the "worker" database server
        '''
        if self.db_password:
            return 'postgresql://{user}:{pw}@{host}:{port}/tissuemaps'.format(
                user=self.db_user, pw=self.db_password,
                host=host, port=port,
            )
        else:
            return 'postgresql://{user}@{host}:{port}/tissuemaps'.format(
                user=self.db_user, host=host, port=port,
            )

    @property
    def items(self):
        '''List[Tuple[str, str or int or bool]]: ``(name, value)`` pairs for
        each configuration parameter in the implemented section
        '''
        return self._config.items(self._section)


class LibraryConfig(TmapsConfig):

    '''`TissueMAPS` configuration specific to the `tmlib` package.'''

    __slots__ = ('_config', '_resource')

    def __init__(self):
        super(LibraryConfig, self).__init__()
        self.modules_home = '~/jtmodules'
        self.formats_home = '~/tmformats'
        self.storage_home = '/storage/filesystem'
        self._resource = None
        self.read()

    @property
    def resource(self):
        '''gc3libs.utils.Struct: information about the enabled *GC3Pie* resource
        '''
        if self._resource is None:
            conf_file = os.path.expanduser('~/.gc3/gc3pie.conf')
            conf = Configuration(conf_file)
            resources = [r for r in conf.resources.values() if r.enabled]
            if len(resources) == 0:
                raise ValueError(
                    'No enabled GC3Pie resource found. Check configuration file: %s'
                    % conf_file
                )
            elif len(resources) > 1:
                raise ValueError(
                    'More than one enabled GC3Pie resource found. '
                    'Check configuration file: %s' % conf_file
                )
            self._resource = resources[0]
        return self._resource

    @property
    def modules_home(self):
        '''str: absolute path to root directory of local copy of
        *TissueMAPS/JtModules* repository (default: ``"~/jtmodules"``)

        Note
        ----
        Assumes a certain repository structure, where *jtmodules* packages
        for different programming languages are placed into a
        language-specific ``src`` subdirectory, such as ``src/python/jtmodules``
        or ``src/matlab/+jtmodules``.
        '''
        return os.path.expandvars(os.path.expanduser(
            self._config.get(self._section, 'modules_home')
        ))

    @modules_home.setter
    def modules_home(self, value):
        if not isinstance(value, basestring):
            raise TypeError(
                'Configuration parameter "modules_home" must have type str.'
            )
        self._config.set(
            self._section, 'modules_home',
            os.path.expandvars(os.path.expanduser(str(value)))
        )

    @property
    def storage_home(self):
        '''str: absolute path to root directory of file system storage'''
        return os.path.expandvars(os.path.expanduser(
            self._config.get(self._section, 'storage_home')
        ))

    @storage_home.setter
    def storage_home(self, value):
        if not isinstance(value, basestring):
            raise TypeError(
                'Configuration parameter "storage_home" must have type str.'
            )
        self._config.set(self._section, 'storage_home', str(value))

    @property
    def formats_home(self):
        '''str: absolute path to the root directory of local copy of
        *TissueMAPS/TmFormats* repository (default: ``"~/tmformats"``)

        Note
        ----
        Assumes a certain repository structure, where *tmformats* package
        is located in the root directory.
        '''
        return os.path.expandvars(os.path.expanduser(
            self._config.get(self._section, 'formats_home')
        ))

    @formats_home.setter
    def formats_home(self, value):
        if not isinstance(value, basestring):
            raise TypeError(
                'Configuration parameter "formats_home" must have '
                'type str.'
            )
        self._config.set(self._section, 'formats_home', str(value))
