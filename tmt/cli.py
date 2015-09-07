# encoding: utf-8
import os
import sys
import traceback
import shutil
import argparse
from abc import ABCMeta
from abc import abstractproperty
from abc import abstractmethod
from . import cfg
from . import utils


def command_line_call(parser):
    '''
    Call a program via the command line.

    Parameters
    ----------
    parser: argparse.ArgumentParser
        argument parser object
    '''
    args = parser.parse_args()

    try:
        if args.handler:
            args.handler(args)
            print '🍺  Done!'
        else:
            parser.print_help()
    except Exception as error:
        sys.stdout.write('😞  Failed!\n')
        sys.stdout.write('Error message: "%s"\n' % str(error))
        for tb in traceback.format_tb(sys.exc_info()[2]):
            sys.stdout.write(tb)


class CommandLineInterface(object):

    '''
    Abstract base class for command line interfaces.

    Note
    ----
    There must be a method for each subparser, where the name of the method
    has to match the name of the corresponding subparser.
    '''

    __metaclass__ = ABCMeta

    def __init__(self, args):
        '''
        Initialize an instance of class CommandLineInterface.

        Parameters
        ----------
        args: argparse.Namespace
            parsed command line arguments

        Note
        ----
        Default configuration settings are overwritten in case a custom
        configuration file is provided via the command line.
        '''
        self.args = args
        self.print_logo()

    @staticmethod
    def _read_cfg_file(filename):
        return utils.read_yaml(filename)

    @property
    def cfg(self):
        '''
        Returns
        -------
        Dict[str, str]
            configuration settings
        '''
        if self.args.cfg_file:
            self._cfg = self.read_cfg_file(self.args.cfg_file)
        else:
            self._cfg = cfg
        return self._cfg

    @abstractproperty
    def name(self):
        '''
        Returns
        -------
        str
            name of the program
        '''
        pass

    @abstractproperty
    def _api_instance(self):
        pass

    @abstractmethod
    def call(args):
        '''
        Handler function that can be called by an argparse subparser.
        Initializes an instance of the class and calls the method corresponding
        to the specified subparser with the parsed command line arguments.

        Parameters
        ----------
        args: argparse.Namespace
            parsed command line arguments

        Note
        ----
        `args` must have the attribute "subparser_name", which specifies the
        name of the subparser.
        '''
        pass

    @abstractmethod
    def print_logo():
        pass

    def joblist(self):
        '''
        Initialize an instance of the API class corresponding to the specific
        command line interface and process arguments of the "joblist" subparser.
        '''
        print 'JOBLIST'
        api = self._api_instance
        print '.  create joblist'
        kwargs = dict()
        kwargs['cfg_file'] = self.args.cfg_file
        if hasattr(self.args, 'batch_size'):
            kwargs['batch_size'] = self.args.batch_size
        # TODO: parse all args
        joblist = api.create_joblist(**kwargs)
        if self.args.print_joblist:
            print '.  joblist:'
            api.print_joblist(joblist)
        else:
            if os.path.exists(api.log_dir):
                if self.args.backup:
                    print '.  create backup of previous submission'
                    shutil.move(api.log_dir, '{name}_backup_{time}'.format(
                                            name=api.log_dir,
                                            time=api.create_datetimestamp()))
                else:
                    print '.  overwrite output of previous submission'
                    shutil.rmtree(api.log_dir)
            print '.  write joblist to file: %s' % api.joblist_file
            api.write_joblist(joblist)

    def run(self):
        '''
        Initialize an instance of the API class corresponding to the specific
        command line interface and process arguments of the "run" subparser.
        '''
        print 'RUN'
        api = self._api_instance
        print '.  read joblist'
        joblist = api.read_joblist()
        print '.  run job'
        batch = joblist[self.args.job-1]
        api.run_job(batch)

    def submit(self):
        '''
        Initialize an instance of the API class corresponding to the specific
        command line interface and process arguments of the "submit" subparser.
        '''
        print 'SUBMIT'
        api = self._api_instance
        print '.  read joblist'
        joblist = api.read_joblist()
        print '.  create jobs'
        jobs = api.create_jobs(joblist)
        print '.  submit and monitor jobs'
        api.submit_jobs(jobs)

    def apply(self):
        '''
        Initialize an instance of the API class corresponding to the specific
        command line interface and process arguments of the "apply" subparser.
        '''
        print 'APPLY'
        api = self._api_instance
        print '.  read jobist'
        joblist = api.read_joblist()
        print '.  apply statistics'
        api.apply_statistics(
            joblist, self.args.channels, self.args.sites, self.args.wells,
            self.args.output_dir)

    def collect(self):
        '''
        Initialize an instance of the API class corresponding to the specific
        command line interface and process arguments of the "collect" subparser.
        '''
        print 'COLLECT'
        api = self._api_instance
        print '.  read jobist'
        joblist = api.read_joblist()
        api.collect_job_output(joblist)

    @staticmethod
    def get_parser_and_subparsers(subparser_names=['run', 'joblist', 'submit'],
                                  level='cycle'):
        '''
        Get an argument parser object and subparser objects with default
        arguments for use in command line interfaces.
        The subparsers objects can be extended with additional subparsers and
        additional arguments can be added to each individual subparser
        (see example).

        Parameters
        ----------
        subparser_names: List[str]
            subparsers that should be returned (by default returns
            "run", "joblist", and "submit" subparsers)
        level: str
            level of the directory tree at which the command line interface
            operates; "cycle" when processing data at the level of an individual
            *cycle* folder or "experiment" when processing data at the level of
            the *experiment* folder, i.e. across multiple *cycle* folders

        Returns
        -------
        Tuple[argparse.Argumentparser and argparse._SubParsersAction]
            parser and subparsers objects
        '''
        parser = argparse.ArgumentParser()
        parser.add_argument(
            '-v', '--version', action='version')
        parser.add_argument(
            '--cfg', dest='cfg_file', type=str,
            help='path to a custom configuration file')

        if not subparser_names:
            raise ValueError('At least one subparser has to specified')

        if level == 'cycle':
            directory = 'cycle_dir'
            directory_help_message = 'path to cycle directory'
        elif level == 'experiment':
            directory = 'experiment_dir'
            directory_help_message = 'path to experiment directory'

        subparsers = parser.add_subparsers(dest='subparser_name')

        if 'run' in subparser_names:
            run_parser = subparsers.add_parser('run')
            run_parser.description = '''
                Run an individual job.
            '''
            run_parser.add_argument(
                '-j', '--job', type=int, required=True,
                help='id of the job that should be processed')
            run_parser.add_argument(directory, help=directory_help_message)

        if 'joblist' in subparser_names:
            joblist_parser = subparsers.add_parser('joblist')
            joblist_parser.description = '''
                Create a list of job descriptions (batches) for parallel
                processing and write it to a file in YAML format.
            '''
            joblist_parser.add_argument(
                '-p', '--print', action='store_true', dest='print_joblist',
                help='print joblist to standard output (don\'t write to file)')
            joblist_parser.add_argument(
                '--backup', action='store_true',
                help='create a backup of the output of a previous submission')
            # NOTE: `batch_size` can be parsed as an additional argument
            joblist_parser.add_argument(directory, help=directory_help_message)

        if 'submit' in subparser_names:
            submit_parser = subparsers.add_parser('submit')
            submit_parser.description = '''
                Submit jobs to the cluster and monitor their processing.
            '''
            submit_parser.add_argument(
                '--no_shared_network', dest='shared_network',
                action='store_false', help='when worker nodes don\'t have \
                access to a shared network')
            submit_parser.add_argument(directory, help=directory_help_message)

        if 'apply' in subparser_names:
            apply_parser = subparsers.add_parser('apply')
            apply_parser.description = '''
                Apply calculated statistics to images.
            '''
            apply_parser.add_argument(
                '-c', '--channels', nargs='+', type=str,
                help='names of channels to process')
            apply_parser.add_argument(
                '-s', '--sites',  nargs='+', type=int,
                help='numbers of sites to process')
            apply_parser.add_argument(
                '-w', '--wells', nargs='+', type=str,
                help='ids of wells to process')
            apply_parser.add_argument(
                '-a', '--all', action='store_true',
                help='when all images should be processed')
            apply_parser.add_argument(
                '-o', '--output_dir', type=str, required=True,
                help='path to output directory')
            apply_parser.add_argument(directory, help=directory_help_message)

        if 'collect' in subparser_names:
            collect_parser = subparsers.add_parser('collect')
            collect_parser.description = '''
                Collect outputs of individual jobs and fuse them.
            '''
            collect_parser.add_argument(
                '-o', '--output_dir', type=str,
                help='path to output directory')
            collect_parser.add_argument(directory, help=directory_help_message)

        return (parser, subparsers)