'''
Settings for the canonical `TissueMAPS` workflow.

In principle, workflow steps can be arranged in arbitrary order and
interdependencies between steps are checked dynamically while the workflow
progresses. If a dependency is not fullfilled upon progression to the
next step, i.e. if a required input has not been generated by another
upstream step, the workflow would stop. However, for the standard
workflow we would like to ensure that the sequence of steps in the workflow
description is correct and thereby prevent submission of an incorrectly
described workflow in the first place.
'''
from collections import OrderedDict
from .description import WorkflowDescription
from .description import WorkflowStageDescription
from .description import WorkflowStepDescription
from ..errors import WorkflowDescriptionError
from .. import utils

import logging

logger = logging.getLogger(__name__)

#: Implemented workflow stages:
#: For more information please refer to the corresponding section in
#: :ref:`introduction <workflow>`
STAGES = [
    'image_conversion', 'image_preprocessing',
    'pyramid_creation', 'image_analysis'
]

STEPS_PER_STAGE = OrderedDict({
    'image_conversion':
        ['metaextract', 'metaconfig', 'imextract'],
    'image_preprocessing':
        ['corilla', 'align'],
    'pyramid_creation':
        ['illuminati'],
    'image_analysis':
        ['jterator']
})
# Note: there could be more than one image analysis pipeline!

#: Dependencies between individual workflow stages.
INTER_STAGE_DEPENDENCIES = OrderedDict({
    'image_conversion': {

    },
    'image_preprocessing': {
        'image_conversion'
    },
    'pyramid_creation': {
        'image_conversion', 'image_preprocessing'
    },
    'image_analysis': {
        'image_conversion', 'image_preprocessing'
    }
})

#: Dependencies between individual workflow steps within one stage.
INTRA_STAGE_DEPENDENCIES = {
    'metaextract': {

    },
    'metaconfig': {
        'metaextract'
    },
    'imextract': {
        'metaconfig'
    }
}


def check_stage_name(stage_name):
    '''
    Check whether a described stage is known.

    Parameters
    ----------
    stage_name: str
        name of the stage

    Raises
    ------
    tmlib.errors.WorkflowDescriptionError
        when `stage_name` is unknown

    See also
    --------
    :py:const:`tmlib.tmaps.canonical.STAGES`
    '''
    known_names = STAGES
    if stage_name not in known_names:
        raise WorkflowDescriptionError(
                'Unknown stage "%s". Known stages are: "%s"'
                % (stage_name, '", "'.join(known_names)))


def check_step_name(step_name, stage_name=None):
    '''
    Check whether a described step is known.

    Parameters
    ----------
    step_name: str
        name of the step
    stage_name: str, optional
        name of the corresponding stage

    Raises
    ------
    tmlib.errors.WorkflowDescriptionError
        when `step_name` is unknown or when step with name `step_name` is not
        part of stage with name `stage_name`

    Note
    ----
    When `stage_name` is provided, it is also checked whether `step_name` is a
    valid step within stage named `stage_name`.

    See also
    --------
    :py:const:`tmlib.tmaps.canonical.STEPS_PER_STAGE`
    '''
    if stage_name:
        known_names = STEPS_PER_STAGE[stage_name]
        if step_name not in known_names:
            raise WorkflowDescriptionError(
                    'Unknown step "%s" for stage "%s". Known steps are: "%s"'
                    % (step_name, stage_name, '", "'.join(known_names)))
    else:
        known_names = utils.flatten(STEPS_PER_STAGE.values())
        if step_name not in known_names:
            raise WorkflowDescriptionError(
                    'Unknown step "%s". Known steps are: "%s"'
                    % (step_name, '", "'.join(known_names)))


class CanonicalWorkflowDescription(WorkflowDescription):

    '''
    Description of a canonical TissueMAPS workflow.
    '''

    def __init__(self, **kwargs):
        '''
        Initialize an instance of class CanonicalWorkflowDescription.

        Parameters
        ----------
        **kwargs: dict, optional
            workflow description as a mapping of as key-value pairs
        '''
        super(CanonicalWorkflowDescription, self).__init__(**kwargs)
        if kwargs:
            for stage in kwargs.get('stages', list()):
                self.add_stage(CanonicalWorkflowStageDescription(**stage))

    def add_stage(self, stage_description):
        '''
        Add an additional stage to the workflow.

        Parameters
        ----------
        stage_description: tmlib.tmaps.canonical.CanonicalWorkflowStageDescription
            description of the stage that should be added

        Raises
        ------
        TypeError
            when `stage_description` doesn't have type
            :py:class:`tmlib.tmaps.canonical.CanonicalWorkflowStageDescription`
        WorkflowDescriptionError
            when stage already exists or when a required step is not described
        '''
        if not isinstance(stage_description, CanonicalWorkflowStageDescription):
            raise TypeError(
                    'Argument "stage_description" must have type '
                    'tmlib.tmaps.canonical.CanonicalWorkflowStageDescription.')
        for stage in self.stages:
            if stage.name == stage_description.name:
                raise WorkflowDescriptionError(
                            'Stage "%s" already exists.'
                            % stage_description.name)
        check_stage_name(stage_description.name)
        for step in stage_description.steps:
            check_step_name(step.name, stage_description.name)
        stage_names = [s.name for s in self.stages]
        if stage_description.name in INTER_STAGE_DEPENDENCIES:
            for dep in INTER_STAGE_DEPENDENCIES[stage_description.name]:
                if dep not in stage_names:
                    logger.warning(
                            'Stage "%s" requires upstream stage "%s"',
                            stage_description.name, dep)
        for name in stage_names:
            if stage_description.name in INTER_STAGE_DEPENDENCIES[name]:
                raise WorkflowDescriptionError(
                            'Stage "%s" must be upstream of stage "%s"'
                            % (stage_description.name, name))
        step_names = [s.name for s in stage_description.steps]
        required_steps = STEPS_PER_STAGE[stage_description.name]
        for name in step_names:
            if name not in required_steps:
                raise WorkflowDescriptionError(
                            'Stage "%s" requires the following steps: "%s" '
                            % '", "'.join(required_steps))
        self.stages.append(stage_description)


class CanonicalWorkflowStageDescription(WorkflowStageDescription):

    '''
    Description of a TissueMAPS workflow stage.
    '''

    def __init__(self, name, steps=None, **kwargs):
        '''
        Initialize an instance of class CanonicalWorkflowStageDescription.

        Parameters
        ----------
        name: str
            name of the stage
        steps: list, optional
            description of individual steps as a mapping of key-value pairs
        **kwargs: dict, optional
            description of a workflow stage in form of key-value pairs
        '''
        check_stage_name(name)
        super(CanonicalWorkflowStageDescription, self).__init__(
                name, steps, **kwargs
        )
        if steps is not None:
            for s in steps:
                self.add_step(CanonicalWorkflowStepDescription(**s))

    def add_step(self, step_description):
        '''
        Add an additional step to the stage.

        Parameters
        ----------
        step_description: tmlib.tmaps.canonical.CanonicalWorkflowStepDescription
            description of the step that should be added

        Raises
        ------
        TypeError
            when `step_description` doesn't have type
            :py:class:`tmlib.tmaps.canonical.CanonicalWorkflowStepDescription`
        workflowDescriptionError
            when step already exists or a required upstream step is missing
        '''
        if not isinstance(step_description, CanonicalWorkflowStepDescription):
            raise TypeError(
                    'Argument "step_description" must have type '
                    'tmlib.cfg.CanonicalWorkflowStepDescription.')
        for step in self.steps:
            if step.name == step_description.name:
                raise WorkflowDescriptionError(
                            'Step "%s" already exists.'
                            % step_description.name)
        name = step_description.name
        step_names = [s.name for s in self.steps]
        if name in INTRA_STAGE_DEPENDENCIES:
            for dep in INTRA_STAGE_DEPENDENCIES[name]:
                if dep not in step_names:
                    raise WorkflowDescriptionError(
                            'Step "%s" requires upstream step "%s"'
                            % (name, dep))
        self.steps.append(step_description)


class CanonicalWorkflowStepDescription(WorkflowStepDescription):

    '''
    Description of a step as part of a TissueMAPS workflow stage.
    '''

    def __init__(self, name, args=None, **kwargs):
        '''
        Initialize an instance of class CanonicalWorkflowStepDescription.

        Parameters
        ----------
        name: str
            name of the step
        args: dict, optional
            arguments of the step as key-value pairs
        **kwargs: dict, optional
            description of the step as key-value pairs

        Raises
        ------
        WorkflowDescriptionError
            when the step is not known
        '''
        check_step_name(name)
        super(CanonicalWorkflowStepDescription, self).__init__(
                name, args, **kwargs
        )
