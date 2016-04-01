import os
import yaml
import glob
import time
import logging
import numpy as np
import datetime
from natsort import natsorted
from abc import ABCMeta
from abc import abstractmethod
from abc import abstractproperty
import gc3libs
from gc3libs.quantity import Duration
from gc3libs.quantity import Memory
from gc3libs.session import Session as GC3PieSession

import tmlib.models
from tmlib import utils
from tmlib.workflow.utils import format_timestamp
from tmlib.workflow.utils import get_task_data
from tmlib.workflow.utils import print_task_status
from tmlib.workflow.utils import log_task_failure
from tmlib.readers import JsonReader
from tmlib.writers import JsonWriter
from tmlib.errors import JobDescriptionError
from tmlib.errors import WorkflowError
from tmlib.workflow.jobs import RunJob
from tmlib.workflow.jobs import RunJobCollection
from tmlib.workflow.jobs import CollectJob
from tmlib.workflow import WorkflowStep
from tmlib.models.utils import DATABASE_URI

logger = logging.getLogger(__name__)


class BasicClusterRoutines(object):

    '''Abstract base class for cluster routines.'''

    __metaclass__ = ABCMeta

    @abstractproperty
    def step_location(self):
        pass

    @utils.autocreate_directory_property
    def log_location(self):
        '''
        Returns
        -------
        str
            location where log files are stored
        '''
        return os.path.join(self.step_location, 'log')

    @property
    def session_location(self):
        '''
        Returns
        -------
        str
            location for a
            `GC3Pie Session <http://gc3pie.readthedocs.org/en/latest/programmers/api/gc3libs/session.html>`_
        '''
        return os.path.join(self.step_location, 'cli_session')

    @property
    def datetimestamp(self):
        '''
        Returns
        -------
        str
            datetime stamp in the form "year-month-day_hour:minute:second"
        '''
        return utils.create_datetimestamp()

    @property
    def timestamp(self):
        '''
        Returns
        -------
        str
            time stamp in the form "hour:minute:second"
        '''
        return utils.create_timestamp()

    @staticmethod
    def _create_batches(li, n):
        # Create a list of lists from a list, where each sublist has length n
        n = max(1, n)
        return [li[i:i + n] for i in range(0, len(li), n)]

    def create_gc3pie_session(self):
        '''
        Create a `GC3Pie session <http://gc3pie.readthedocs.org/en/latest/programmers/api/gc3libs/session.html>`_
        for job persistence.

        Returns
        -------
        gc3libs.session.Session
            SQL-based session 
        '''
        def get_time(task, time_attr):
            def get_recursive(_task, duration):
                if hasattr(_task, 'tasks'):
                    return np.sum([
                        get_recursive(t, duration) for t in _task.tasks
                    ])
                else:
                    return getattr(_task.execution, time_attr).to_timedelta()
            return get_recursive(task, datetime.timedelta(seconds=0))

        logger.info('create session')
        gc3pie_session_uri = DATABASE_URI.replace('postgresql', 'postgres')
        table_columns = tmlib.models.Task.__table__.columns
        return GC3PieSession(
            self.session_location,
            store_url=gc3pie_session_uri,
            table_name='tasks',
            extra_fields={
                table_columns['name']:
                    lambda task: task.jobname,
                table_columns['exitcode']:
                    lambda task: task.execution.exitcode,
                table_columns['time']:
                    lambda task: get_time(task, 'duration'),
                table_columns['memory']:
                    lambda task: task.execution.max_used_memory.amount(Memory.MB),
                table_columns['cpu_time']:
                    lambda task: get_time(task, 'used_cpu_time'),
                table_columns['submission_id']:
                    lambda task: task.submission_id,
                table_columns['type']:
                    lambda task: type(task).__name__
            }
        )

    def create_gc3pie_engine(self):
        '''
        Create an `Engine` instance for submitting jobs for parallel
        processing.

        Returns
        -------
        gc3libs.core.Engine
            engine
        '''
        logger.debug('create engine')
        engine = gc3libs.create_engine()
        # Put all output files in the same directory
        logger.debug('store stdout/stderr in common output directory')
        engine.retrieve_overwrites = True
        return engine

    def submit_jobs(self, jobs, engine, monitoring_interval=5,
                    monitoring_depth=1, n_submit=2000):
        '''
        Submit jobs to a cluster and continuously monitor their progress.

        Parameters
        ----------
        jobs: tmlib.tmaps.workflow.WorkflowStep
            jobs that should be submitted
        engine: gc3libs.core.Engine
            engine that should submit the jobs
        monitoring_interval: int, optional
            monitoring interval in seconds (default: ``5``)
        monitoring_depth: int, optional
            recursion depth for job monitoring, i.e. in which detail subtasks
            in the task tree should be monitored (default: ``1``)
        n_submit: int, optional
            number of jobs that will be submitted at once (default: ``2000``)

        Returns
        -------
        dict
            information about each job

        Warning
        -------
        This method is intended for interactive use via the command line only.
        '''
        logger.debug('monitoring interval: %d seconds' % monitoring_interval)
        logger.debug('monitoring depth: %d' % monitoring_depth)
        if monitoring_depth < 0:
            monitoring_depth = 0

        # Limit the total number of jobs that can be submitted simultaneously
        logger.debug('set maximum number of submitted jobs to %d', n_submit)
        engine.max_submitted = n_submit
        engine.max_in_flight = n_submit

        logger.debug('add jobs %s to engine', jobs)
        engine.add(jobs)

        # periodically check the status of submitted jobs
        t_submitted = time.time()
        break_next = False
        while True:

            time.sleep(monitoring_interval)
            logger.debug('wait %d seconds', monitoring_interval)

            t_elapsed = time.time() - t_submitted
            logger.info('duration: %s', format_timestamp(t_elapsed))

            logger.info('progress...')
            engine.progress()

            status_data = get_task_data(jobs)
            print_task_status(status_data, monitoring_depth)

            if break_next:
                break

            if (jobs.execution.state == gc3libs.Run.State.TERMINATED or
                    jobs.execution.state == gc3libs.Run.State.STOPPED):
                break_next = True
                engine.progress()  # one more iteration to update status_data

        status_data = get_task_data(jobs)
        log_task_failure(status_data, logger)

        return status_data


class ClusterRoutines(BasicClusterRoutines):

    '''Abstract base class for API classes, which provide methods for 
    cluster routines, such as creation, submission, and monitoring of jobs.
    '''

    __metaclass__ = ABCMeta

    def __init__(self, experiment_id, step_name, verbosity):
        '''
        Parameters
        ----------
        experiment_id: int
            ID of the processed experiment
        step_name: str
            name of the corresponding program (command line interface)
        verbosity: int
            logging level

        Attributes
        ----------
        experiment_id: int
            ID of the processed experiment
        step_name: str
            name of the corresponding program (command line interface)
        verbosity: int
            logging level
        experiment_location: str
            absolute path to experiment root directory
        '''
        super(ClusterRoutines, self).__init__()
        self.experiment_id = experiment_id
        self.step_name = step_name
        self.verbosity = verbosity
        with tmlib.models.utils.Session() as session:
            experiment = session.query(tmlib.models.Experiment).\
                get(self.experiment_id)
            self.experiment_location = experiment.location

    @utils.autocreate_directory_property
    def workflow_location(self):
        '''str: location were workflow related data is stored'''
        return os.path.join(self.experiment_location, 'workflow')

    @utils.autocreate_directory_property
    def step_location(self):
        '''str: location were step-specific data is stored'''
        return os.path.join(self.workflow_location, self.step_name)

    @utils.autocreate_directory_property
    def job_descriptions_location(self):
        '''str: location where job description files are stored'''
        return os.path.join(self.step_location, 'job_descriptions')

    def get_job_descriptions_from_files(self):
        '''Get job descriptions from files and combine them into
        the format required by the `create_jobs()` method.

        Returns
        -------
        dict
            job descriptions

        Raises
        ------
        :py:exc:`tmlib.errors.JobDescriptionError`
            when no job descriptor files are found
        '''
        job_descriptions = dict()
        job_descriptions['run'] = list()
        run_job_files = glob.glob(
            os.path.join(self.job_descriptions_location, '*_run_*.job.json')
        )
        if not run_job_files:
            raise JobDescriptionError('No job descriptor files found.')
        collect_job_files = glob.glob(
            os.path.join(self.job_descriptions_location, '*_collect.job.json')
        )

        for f in run_job_files:
            batch = self.read_job_file(f)
            job_descriptions['run'].append(batch)
        if collect_job_files:
            f = collect_job_files[0]
            job_descriptions['collect'] = self.read_job_file(f)

        return job_descriptions

    def get_log_output_from_files(self, job_id):
        '''Get log outputs (standard output and error) from files.

        Parameters
        ----------
        job_id: int
            one-based job identifier number

        Returns
        -------
        Dict[str, str]
            "stdout" and "stderr" for the given job

        Note
        ----
        In case there are several log files present for the given the most
        recent one will be used (sorted by submission date and time point).
        '''
        if job_id is not None:
            stdout_files = glob.glob(
                os.path.join(self.log_location, '*_run*_%.6d*.out' % job_id)
            )
            stderr_files = glob.glob(
                os.path.join(self.log_location, '*_run*_%.6d*.err' % job_id)
            )
            if not stdout_files or not stderr_files:
                raise IOError('No log files found for run job # %d' % job_id)
        else:
            stdout_files = glob.glob(
                os.path.join(self.log_location, '*_collect*.out')
            )
            stderr_files = glob.glob(
                os.path.join(self.log_location, '*_collect_*.err')
            )
            if not stdout_files or not stderr_files:
                raise IOError('No log files found for collect job')
        # Take the most recent log files
        log = dict()
        with open(natsorted(stdout_files)[-1], 'r') as f:
            log['stdout'] = f.read()
        with open(natsorted(stderr_files)[-1], 'r') as f:
            log['stderr'] = f.read()
        return log

    def list_output_files(self, job_descriptions):
        '''List all output files that should be created by the step.

        Parameters
        ----------
        job_descriptions: List[dict]
            job descriptions
        '''
        files = list()
        if job_descriptions['run']:
            run_files = utils.flatten([
                j['outputs'].values() for j in job_descriptions['run']
            ])
            if all([isinstance(f, list) for f in run_files]):
                run_files = utils.flatten(run_files)
                if all([isinstance(f, list) for f in run_files]):
                    run_files = utils.flatten(run_files)
                files.extend(run_files)
            else:
                files.extend(run_files)
        if 'collect' in job_descriptions.keys():
            outputs = job_descriptions['collect']['outputs']
            collect_files = utils.flatten(outputs.values())
            if all([isinstance(f, list) for f in collect_files]):
                collect_files = utils.flatten(collect_files)
                if all([isinstance(f, list) for f in collect_files]):
                    collect_files = utils.flatten(collect_files)
                files.extend(collect_files)
            else:
                files.extend(collect_files)
        return files

    def list_input_files(self, job_descriptions):
        '''Provide a list of all input files that are required by the step.

        Parameters
        ----------
        job_descriptions: List[dict]
            job descriptions
        '''
        files = list()
        if job_descriptions['run']:
            run_files = utils.flatten([
                j['inputs'].values() for j in job_descriptions['run']
            ])
            if all([isinstance(f, list) for f in run_files]):
                run_files = utils.flatten(run_files)
                if all([isinstance(f, list) for f in run_files]):
                    run_files = utils.flatten(run_files)
                files.extend(run_files)
            elif any([isinstance(f, dict) for f in run_files]):
                files.extend(utils.flatten([
                    utils.flatten(f.values())
                    for f in run_files if isinstance(f, dict)
                ]))
            else:
                files.extend(run_files)
        return files

    def build_run_job_filename(self, job_id):
        '''
        Parameters
        ----------
        job_id: int
            one-based job identifier number

        Returns
        -------
        str
            absolute path to the file that holds the description of the
            job with the given `job_id`

        Note
        ----
        The total number of jobs is limited to 10^6.
        '''
        return os.path.join(
            self.job_descriptions_location,
            '%s_run_%.6d.job.json' % (self.step_name, job_id)
        )

    def build_collect_job_filename(self):
        '''
        Returns
        -------
        str
            absolute path to the file that holds the description of the
            job with the given `job_id`
        '''
        return os.path.join(
            self.job_descriptions_location,
            '%s_collect.job.json' % self.step_name
        )

    def _make_paths_absolute(self, batch):
        for key, value in batch['inputs'].items():
            if isinstance(value, dict):
                for k, v in batch['inputs'][key].items():
                    if isinstance(v, list):
                        batch['inputs'][key][k] = [
                            os.path.join(self.experiment_location, sub_v)
                            for sub_v in v
                        ]
                    else:
                        batch['inputs'][key][k] = os.path.join(
                            self.experiment_location, v
                        )
            elif isinstance(value, list):
                if len(value) == 0:
                    continue
                if isinstance(value[0], list):
                    for i, v in enumerate(value):
                        batch['inputs'][key][i] = [
                            os.path.join(self.experiment_location, sub_v)
                            for sub_v in v
                        ]
                else:
                    batch['inputs'][key] = [
                        os.path.join(self.experiment_location, v)
                        for v in value
                    ]
            else:
                raise TypeError(
                    'Value of "inputs" must have type list or dict.'
                )
        for key, value in batch['outputs'].items():
            if isinstance(value, list):
                if len(value) == 0:
                    continue
                if isinstance(value[0], list):
                    for i, v in enumerate(value):
                        batch['outputs'][key][i] = [
                            os.path.join(self.experiment_location, sub_v)
                            for sub_v in v
                        ]
                else:
                    batch['outputs'][key] = [
                        os.path.join(self.experiment_location, v)
                        for v in value
                    ]
            elif isinstance(value, basestring):
                batch['outputs'][key] = os.path.join(
                    self.experiment_location, value
                )
            else:
                raise TypeError(
                    'Value of "outputs" must have type list or str.'
                )
        return batch

    def read_job_file(self, filename):
        '''Read job description from JSON file.

        Parameters
        ----------
        filename: str
            absolute path to the *.job* file that contains the description
            of a single job

        Returns
        -------
        dict
            job description (batch)

        Raises
        ------
        tmlib.errors.WorkflowError
            when `filename` does not exist

        Note
        ----
        The relative paths for "inputs" and "outputs" are made absolute.
        '''
        if not os.path.exists(filename):
            raise WorkflowError(
                'Job description file does not exist: %s.\n'
                'Initialize the step first by calling the "init" method.'
                % filename
            )
        with JsonReader(filename) as f:
            batch = f.read()
            return self._make_paths_absolute(batch)

    @staticmethod
    def _check_io_description(job_descriptions):
        if not all([
                isinstance(batch['inputs'], dict)
                for batch in job_descriptions['run']]):
            raise TypeError('"inputs" must have type dictionary')
        if not all([
                isinstance(batch['inputs'].values(), list)
                for batch in job_descriptions['run']]):
            raise TypeError('Elements of "inputs" must have type list')
        if not all([
                isinstance(batch['outputs'], dict)
                for batch in job_descriptions['run']]):
            raise TypeError('"outputs" must have type dictionary')
        if not all([
                all([isinstance(o, list) for o in batch['outputs'].values()])
                for batch in job_descriptions['run']]):
            raise TypeError('Elements of "outputs" must have type list.')
        if 'collect' in job_descriptions:
            batch = job_descriptions['collect']
            if not isinstance(batch['inputs'], dict):
                raise TypeError('"inputs" must have type dictionary')
            if not isinstance(batch['inputs'].values(), list):
                raise TypeError('Elements of "inputs" must have type list')
            if not isinstance(batch['outputs'], dict):
                raise TypeError('"outputs" must have type dictionary')
            if not all([isinstance(o, list) for o in batch['outputs'].values()]):
                raise TypeError('Elements of "outputs" must have type list')

    def _make_paths_relative(self, batch):
        for key, value in batch['inputs'].items():
            if isinstance(value, dict):
                for k, v in batch['inputs'][key].items():
                    if isinstance(v, list):
                        batch['inputs'][key][k] = [
                            os.path.relpath(sub_v, self.experiment_location)
                            for sub_v in v
                        ]
                    else:
                        batch['inputs'][key][k] = os.path.relpath(
                            v, self.experiment_location
                        )
            elif isinstance(value, list):
                if len(value) == 0:
                    continue
                if isinstance(value[0], list):
                    for i, v in enumerate(value):
                        batch['inputs'][key][i] = [
                            os.path.relpath(sub_v, self.experiment_location)
                            for sub_v in v
                        ]
                else:
                    batch['inputs'][key] = [
                        os.path.relpath(v, self.experiment_location)
                        for v in value
                    ]
            else:
                raise TypeError(
                    'Value of "inputs" must have type list or dict.'
                )
        for key, value in batch['outputs'].items():
            if isinstance(value, list):
                if len(value) == 0:
                    continue
                if isinstance(value[0], list):
                    for i, v in enumerate(value):
                        batch['outputs'][key][i] = [
                            os.path.relpath(sub_v, self.experiment_location)
                            for sub_v in v
                        ]
                else:
                    batch['outputs'][key] = [
                        os.path.relpath(v, self.experiment_location)
                        for v in value
                    ]
            elif isinstance(value, basestring):
                batch['outputs'][key] = os.path.relpath(
                    value, self.experiment_location
                )
            else:
                raise TypeError(
                    'Value of "outputs" must have type list or str.'
                )
        return batch

    def write_job_files(self, job_descriptions):
        '''Write job descriptions to files as JSON.

        Parameters
        ----------
        job_descriptions: List[dict]
            job descriptions

        Note
        ----
        The paths for "inputs" and "outputs" are made relative to the
        experiment directory.
        '''
        if not os.path.exists(self.job_descriptions_location):
            logger.debug('create directories for job descriptor files')
            os.makedirs(self.job_descriptions_location)
        self._check_io_description(job_descriptions)

        for batch in job_descriptions['run']:
            logger.debug('make paths relative to experiment directory')
            batch = self._make_paths_relative(batch)
            job_file = self.build_run_job_filename(batch['id'])
            with JsonWriter(job_file) as f:
                f.write(batch)
        if 'collect' in job_descriptions.keys():
            batch = self._make_paths_relative(job_descriptions['collect'])
            job_file = self.build_collect_job_filename()
            with JsonWriter(job_file) as f:
                f.write(batch)

    def _build_run_command(self, batch):
        command = [self.step_name]
        command.extend(['-v' for x in xrange(self.verbosity)])
        command.append(self.experiment_id)
        command.extend(['run', '--job', str(batch['id'])])
        return command

    def _build_collect_command(self):
        command = [self.step_name]
        command.extend(['-v' for x in xrange(self.verbosity)])
        command.append(self.experiment_id)
        command.extend(['collect'])
        return command

    @abstractmethod
    def run_job(self, batch):
        '''Run an individual job.

        Parameters
        ----------
        batch: dict
            description of the job
        '''
        pass

    @abstractmethod
    def collect_job_output(self, batch):
        '''Collect the output of jobs and fuse them if necessary.

        Parameters
        ----------
        job_descriptions: List[dict]
            job descriptions
        **kwargs: dict
            additional variable input arguments as key-value pairs
        '''
        pass

    @abstractmethod
    def create_batches(self, args):
        '''Create job descriptions with information required for the creation
        and processing of individual jobs.

        Parameters
        ----------
        args: tmlib.args.Args
            an instance of an implemented subclass of the `Args` base class

        There are two phases:
            * *run* phase: collection of tasks that are processed in parallel
            * *collect* phase: a single task that is processed once the
              *run* phase is terminated successfully

        Each batch (element of the *run* job_descriptions) must provide the
        following key-value pairs:
            * "id": one-based job identifier number (*int*)
            * "inputs": absolute paths to input files required to run the job
              (Dict[*str*, List[*str*]])
            * "outputs": absolute paths to output files produced the job
              (Dict[*str*, List[*str*]])

        In case a *collect* job is required, the corresponding batch must
        provide the following key-value pairs:
            * "inputs": absolute paths to input files required to collect job
              output of the *run* phase (Dict[*str*, List[*str*]])
            * "outputs": absolute paths to output files produced by the job
              (Dict[*str*, List[*str*]])

        A *collect* job description can have the optional key "removals", which
        provides a list of strings indicating which of the inputs are removed
        during the *collect* phase.

        A complete job_descriptions has the following structure::

            {
                "run": [
                    {
                        "id": ,            # int
                        "inputs": ,        # list or dict,
                        "outputs": ,       # list or dict,
                    },
                    ...
                ]
                "collect":
                    {
                        "inputs": ,        # list or dict,
                        "outputs": ,       # list or dict
                    }
            }

        Returns
        -------
        Dict[str, List[dict] or dict]
            job descriptions
        '''
        pass

    def print_job_descriptions(self, job_descriptions):
        '''
        Print `job_descriptions` to standard output in YAML format.

        Parameters
        ----------
        job_descriptions: Dict[List[dict]]
            description of inputs and outputs or individual jobs
        '''
        print yaml.safe_dump(job_descriptions, default_flow_style=False)

    def create_jobs(self, job_descriptions,
                    duration=None, memory=None, cores=None):
        '''
        Create jobs that can be submitted for processing.

        Parameters
        ----------
        job_descriptions: Dict[List[dict]]
            description of inputs and outputs of individual computational jobs
        duration: str, optional
            computational time that should be allocated for a single job;
            in HH:MM:SS format (default: ``None``)
        memory: int, optional
            amount of memory in Megabyte that should be allocated for a single
            job (default: ``None``)
        cores: int, optional
            number of CPU cores that should be allocated for a single job
            (default: ``None``)

        Returns
        -------
        tmlib.tmaps.workflow.WorkflowStep
            collection of jobs
        '''
        logger.info('create workflow step')

        with tmlib.models.utils.Session() as session:
            experiment = session.query(tmlib.models.Experiment).\
                get(self.experiment_id)
            submission = tmlib.models.Submission(experiment_id=experiment.id)
            session.add(submission)
            session.flush()

            if 'run' in job_descriptions.keys():
                logger.info('create jobs for "run" phase')
                run_jobs = RunJobCollection(
                    step_name=self.step_name,
                    submission_id=submission.id
                )
                for i, batch in enumerate(job_descriptions['run']):
                    # Add "submission_id" to object so that it can be injected
                    # into the corresponding database table
                    job = RunJob(
                        step_name=self.step_name,
                        arguments=self._build_run_command(batch),
                        output_dir=self.log_location,
                        job_id=batch['id'],
                        submission_id=submission.id
                    )
                    if duration:
                        job.requested_walltime = Duration(duration)
                    if memory:
                        job.requested_memory = Memory(memory, Memory.MB)
                    if cores:
                        if not isinstance(cores, int):
                            raise TypeError(
                                'Argument "cores" must have type int.'
                            )
                        if not cores > 0:
                            raise ValueError(
                                'The value of "cores" must be positive.'
                            )
                        job.requested_cores = cores

                    run_jobs.add(job)

            else:
                run_jobs = None

            if 'collect' in job_descriptions.keys():
                logger.info('create job for "collect" phase')
                batch = job_descriptions['collect']

                collect_job = CollectJob(
                    step_name=self.step_name,
                    arguments=self._build_collect_command(),
                    output_dir=self.log_location,
                    submission_id=submission.id
                )
                collect_job.requested_walltime = Duration('02:00:00')
                collect_job.requested_memory = Memory(4000, Memory.MB)

            else:
                collect_job = None

            jobs = WorkflowStep(
                name=self.step_name,
                run_jobs=run_jobs,
                collect_job=collect_job,
                submission_id=submission.id
            )

        return jobs
