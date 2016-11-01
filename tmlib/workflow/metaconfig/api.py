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
import logging
import numpy as np
import pandas as pd
import bioformats

import tmlib.models as tm
from tmlib.workflow.metaconfig import metadata_handler_factory
from tmlib.workflow.metaconfig import metadata_reader_factory
from tmlib.workflow.metaconfig import get_microscope_type_regex
from tmlib.workflow.api import ClusterRoutines
from tmlib.errors import MetadataError
from tmlib.workflow import register_step_api

logger = logging.getLogger(__name__)


@register_step_api('metaconfig')
class MetadataConfigurator(ClusterRoutines):

    '''Class for configuration of microscope image metadata.

    It provides methods for conversion of metadata extracted from heterogeneous
    microscope file formats into a `TissueMAPS`-specific schema.
    The original metadata has to be available in OMEXML format according to the
    `OME schema <http://www.openmicroscopy.org/Schemas/Documentation/Generated/OME-2015-01/ome.html>`_.

    The class further provides methods to complement the metadata retrieved
    via `Bio-Formats <http://www.openmicroscopy.org/site/products/bio-formats>`_
    with information available from of additional microscope-specific metadata
    files and/or user input.

    '''

    def __init__(self, experiment_id, verbosity, **kwargs):
        '''
        Parameters
        ----------
        experiment_id: int
            ID of the processed experiment
        verbosity: int
            logging level
        **kwargs: dict
            ignored keyword arguments
        '''
        super(MetadataConfigurator, self).__init__(experiment_id, verbosity)

    def create_batches(self, args):
        '''Creates job descriptions for parallel processing.

        Parameters
        ----------
        args: tmlib.workflow.metaconfig.args.MetaconfigBatchArgs
            step-specific batch arguments

        Returns
        -------
        Dict[str, List[dict] or dict]
            job descriptions
        '''
        job_descriptions = dict()
        job_descriptions['run'] = list()
        job_count = 0

        with tm.utils.ExperimentSession(self.experiment_id) as session:
            experiment = session.query(tm.Experiment).one()
            for acq in session.query(tm.Acquisition):
                job_count += 1
                description = {
                    'id': job_count,
                    'inputs': {
                        'microscope_metadata_files': [
                            os.path.join(
                                acq.microscope_metadata_location, f.name
                            )
                            for f in acq.microscope_metadata_files
                        ]
                    },
                    'outputs': {},
                    'microscope_image_file_ids': [
                        f.id for f in acq.microscope_image_files
                    ],
                    'microscope_type': experiment.microscope_type,
                    'regex': args.regex,
                    'acquisition_id': acq.id,
                    'n_vertical': args.n_vertical,
                    'n_horizontal': args.n_horizontal,
                    'stitch_layout': args.stitch_layout
                }
                job_descriptions['run'].append(description)

            job_descriptions['collect'] = {
                'inputs': dict(),
                'outputs': dict()
            }

        return job_descriptions

    def delete_previous_job_output(self):
        '''Deletes all instances of class :class:`tm.Cycle`,
        :class:`tm.Well`, and :class:`tm.Channel` as
        well as all children for the processed experiment.
        '''
        with tm.utils.ExperimentSession(self.experiment_id) as session:
            logger.info('delete existing channels')
            session.drop_and_recreate(tm.ChannelLayerTile)
            session.drop_and_recreate(tm.ChannelLayer)
            session.drop_and_recreate(tm.Channel)
            logger.info('delete existing cycles')
            # NOTE: the delete also triggers the removal of the files from disk
            session.drop_and_recreate(tm.ChannelImageFile)
            session.drop_and_recreate(tm.Cycle)
            logger.info('delete existing wells')
            session.drop_and_recreate(tm.Site)
            session.drop_and_recreate(tm.Well)
            logger.debug('delete existing image file mappings')
            session.drop_and_recreate(tm.ImageFileMapping)

    def run_job(self, batch):
        '''Configures OMEXML metadata extracted from microscope image files and
        complements it with metadata retrieved from additional microscope
        metadata files and/or user input.

        The actual processing is delegated to a format-specific implementation
        of :class:`tmlib.workflow.metaconfig.base.MetadataHandler`.

        Parameters
        ----------
        batch: dict
            job description

        See also
        --------
        :mod:`tmlib.workflow.metaconfig.cellvoyager`
        '''
        MetadataReader = metadata_reader_factory(batch['microscope_type'])

        regexp = batch.get('regex', '')
        if not regexp:
            regexp = get_microscope_type_regex(
                batch['microscope_type'], as_string=True
            )[0]
        with tm.utils.ExperimentSession(self.experiment_id) as session:
            experiment = session.query(tm.Experiment).one()
            plate_dimensions = experiment.plates[0].dimensions
            acquisition = session.query(tm.Acquisition).\
                get(batch['acquisition_id'])
            metadata_filenames = [
                f.location for f in acquisition.microscope_metadata_files
            ]
            omexml_images = {
                f.name: bioformats.OMEXML(f.omexml)
                for f in acquisition.microscope_image_files
            }

        with MetadataReader() as mdreader:
            omexml_metadata = mdreader.read(
                metadata_filenames, omexml_images.keys()
            )

        MetadataHandler = metadata_handler_factory(batch['microscope_type'])
        mdhandler = MetadataHandler(omexml_images, omexml_metadata)
        mdhandler.configure_omexml_from_image_files()
        mdhandler.configure_omexml_from_metadata_files(regexp)
        missing = mdhandler.determine_missing_metadata()
        if missing:
            logger.warning(
                'required metadata information is missing: "%s"',
                '", "'.join(missing)
            )
            logger.info(
                'try to retrieve missing metadata from filenames '
                'using regular expression'
            )
            mdhandler.configure_metadata_from_filenames(
                plate_dimensions=plate_dimensions,
                regex=batch['regex']
            )
            if (batch['regex'] is None and
                    mdhandler.IMAGE_FILE_REGEX_PATTERN is None):
                logger.warning(
                    'required metadata information is still missing: "%s"',
                    '", "'.join(missing)
                )
                logger.info(
                    'you can provide a regular expression in order to '
                    'retrieve the missing information from filenames'
                )
        missing = mdhandler.determine_missing_metadata()
        if missing:
            raise MetadataError(
                'The following metadata information is missing:\n"%s"\n'
                % '", "'.join(missing)
            )
        # Once we have collected basic metadata such as information about
        # channels and focal planes, we try to determine the relative position
        # of images within the acquisition grid
        try:
            logger.info(
                'try to determine grid coordinates from microscope '
                'stage positions'
            )
            mdhandler.determine_grid_coordinates_from_stage_positions()
        except MetadataError as error:
            logger.warning(
                'microscope stage positions are not available: "%s"'
                % str(error)
            )
            logger.info(
                'try to determine grid coordinates from provided stitch layout'
            )
            mdhandler.determine_grid_coordinates_from_layout(
                stitch_layout=batch['stitch_layout'],
                stitch_dimensions=(batch['n_vertical'], batch['n_horizontal'])
            )

        mdhandler.group_metadata_per_zstack()

        # Create consistent zero-based ids
        mdhandler.update_channel()
        mdhandler.assign_acquisition_site_indices()
        md = mdhandler.remove_redundant_columns()
        fmap = mdhandler.create_image_file_mapping()

        logger.info('create database entries')
        for w in np.unique(md.well_name):
            logger.info('create well "%s"', w)

            with tm.utils.ExperimentSession(self.experiment_id) as session:
                acquisition = session.query(tm.Acquisition).\
                    get(batch['acquisition_id'])

                w_index = md.well_name == w
                well = session.get_or_create(
                    tm.Well,
                    plate_id=acquisition.plate.id, name=w
                )

                file_mappings = list()
                for s in np.unique(md.loc[w_index, 'site']):
                    logger.debug('create site #%d', s)
                    s_index = md.site == s
                    y = md.loc[s_index, 'well_position_y'].values[0]
                    x = md.loc[s_index, 'well_position_x'].values[0]
                    height = md.loc[s_index, 'height'].values[0]
                    width = md.loc[s_index, 'width'].values[0]
                    # We need the id because it's a foreign key on file mappings.
                    # Therefore, we have to insert/update one by one.
                    site = session.get_or_create(
                        tm.Site,
                        y=y, x=x, height=height, width=width, well_id=well.id
                    )

                    for index, i in md.ix[s_index].iterrows():
                        file_mappings.append(
                            tm.ImageFileMapping(
                                tpoint=i.tpoint,
                                site_id=site.id, map=fmap[index],
                                wavelength=i.channel_name,
                                bit_depth=i.bit_depth,
                                acquisition_id=acquisition.id
                            )
                        )

                # NOTE: bulk_save_objects() can handle inserts and updates and
                # updates only rows that have changed.
                session.bulk_save_objects(file_mappings)

    def collect_job_output(self, batch):
        '''Assigns registered image files from different acquisitions to
        separate *cycles*. If an acquisition includes multiple time points,
        a separate *cycle* is created for each time point.
        The mapping from *acquisitions* to *cycles* is consequently
        1 -> n, where n is the number of time points per acquisition (n >= 1).

        Whether acquisition time points will be interpreted as actual
        time points in a time series depends on the value of
        :attr:`tm.Experiment.plate_acquisition_mode`.

        Parameters
        ----------
        batch: dict
            description of the *collect* job
        '''
        with tm.utils.ExperimentSession(self.experiment_id) as session:
            # We need to do this per plate to ensure correct indices
            # TODO: check plates have similar channels, etc
            experiment = session.query(tm.Experiment).one()
            acquisition_mode = experiment.plate_acquisition_mode
            channels_location = experiment.channels_location
            logger.info('plates were acquired in mode "%s"', acquisition_mode)
            is_time_series = acquisition_mode == 'basic'
            if is_time_series:
                logger.info('time points are interpreted as time series')
            is_multiplexing = acquisition_mode == 'multiplexing'
            if is_multiplexing:
                logger.info('time points are interpreted as multiplexing cycles')

        with tm.utils.ExperimentSession(self.experiment_id) as session:
            for plate in session.query(tm.Plate):
                logger.info('configure plate "%s"', plate.name)
                t_index = 0
                w_index = 0
                c_index = 0
                for acquisition in plate.acquisitions:
                    logger.info('configure acquisition "%s"', acquisition.name)
                    df = pd.DataFrame(
                        session.query(
                            tm.ImageFileMapping.tpoint,
                            tm.ImageFileMapping.wavelength,
                            tm.ImageFileMapping.bit_depth
                        ).
                        filter_by(acquisition_id=acquisition.id).
                        all()
                    )
                    tpoints = np.unique(df.tpoint)
                    wavelengths = np.unique(df.wavelength)
                    bit_depth = np.unique(df.bit_depth)
                    if len(bit_depth) == 1:
                        bit_depth = bit_depth[0]
                    else:
                        raise MetadataError(
                            'Bit depth must be the same for all images.'
                        )
                    for t in tpoints:
                        logger.debug('time point #%d', t)
                        cycle = session.get_or_create(
                            tm.Cycle,
                            index=c_index, tpoint=t_index, plate_id=plate.id
                        )

                        for w in wavelengths:
                            logger.debug('configure wavelength "%s"', w)
                            if is_multiplexing:
                                name = 'cycle-%d_wavelength-%s' % (c_index, w)
                            else:
                                name = 'wavelength-%s' % w
                            channel = session.get_or_create(
                                tm.Channel,
                                name=name, index=w_index, wavelength=w,
                                bit_depth=bit_depth
                            )

                            file_mapping_ids = session.query(
                                    tm.ImageFileMapping.id
                                ).\
                                filter_by(
                                    tpoint=t, wavelength=w,
                                    acquisition_id=acquisition.id
                                )
                            logger.info(
                                'update time point and channel metadata '
                                'of file mappings: tpoint=%d, channel=%d',
                                t_index, channel.index
                            )
                            session.bulk_update_mappings(
                                tm.ImageFileMapping, [
                                  {
                                    'id': i.id,
                                    'tpoint': t_index,
                                    'cycle_id': cycle.id,
                                    'channel_id': channel.id
                                  } for i in file_mapping_ids
                                ]
                            )

                            w_index += 1

                        if is_time_series:
                            t_index += 1

                        c_index += 1