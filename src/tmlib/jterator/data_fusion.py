import os
import re
from collections import defaultdict
import logging
from ..readers import DatasetReader
from ..writers import DatasetWriter
from ..errors import DataError

logger = logging.getLogger(__name__)


def combine_datasets(input_files, output_file, delete_input_files=False):
    '''
    Combine data stored across several HDF5 files into one HDF5 file.

    Parameters
    ----------
    input_files: List[str]
        paths to Jterator data files that were generated by individual jobs
    output_file: str
        path to the final data file that should contain the entire datasets
    delete_input_files: bool, optional
        whether `input_files` should be deleted after their content is fused
        into `output_file` (default: ``False``)

    Returns
    -------
    Dict[str, pandas.core.frame.DataFrame or numpy.ndarray]
        fused datasets for each dataset; the key specifies the full path to the
        location of the dataset within the HDF5 file
    '''

    # Get paths to datasets in the file recursively
    # NOTE: This approach is not general, but rather assumes a defined structure
    logger.info('determine names and data types of the final datasets')
    # n_features = defaultdict(int)
    datasets = defaultdict()
    with DatasetReader(input_files[0]) as f:

        metadata_datasets = f.list_datasets('/metadata')
        datasets['metadata'] = [
            {
                'path': '/metadata/%s' % d,
                'dtype': f.get_type('/metadata/%s' % d),
            }
            for d in metadata_datasets
        ]

        object_names = f.list_groups('/objects')
        for obj_name in object_names:
            datasets[obj_name] = defaultdict(list)
            features_path = 'objects/%s/features' % obj_name
            if f.exists(features_path):
                features_datasets = f.list_datasets(features_path)
                datasets[obj_name]['features'] = [
                    {
                        'path': '%s/%s' % (features_path, d),
                        'dtype': f.get_type('%s/%s' % (features_path, d))
                    }
                    for d in features_datasets
                ]
                # if features_datasets:
                #     dims = f.get_dimensions(datasets[obj_name]['features'][0])
                #     if len(dims) > 1:
                #         n_features[obj_name] = dims[1]
                #     elif len(dims) == 1:
                #         n_features[obj_name] = 1
                #     else:
                #         n_features[obj_name] = None
                # else:
                #     n_features[obj_name] = None

            segmentation_path = 'objects/%s/segmentation' % obj_name
            if f.exists(segmentation_path):
                segmentation_datasets = f.list_datasets(segmentation_path)
                datasets[obj_name]['segmentation'] = [
                    {
                        'path': '%s/%s' % (segmentation_path, d),
                        'dtype': f.get_type('%s/%s' % (segmentation_path, d))
                    }
                    for d in segmentation_datasets
                ]
                segmentation_subpaths = [
                    '%s/%s' % (segmentation_path, p)
                    for p in f.list_groups(segmentation_path)
                ]
                for path in segmentation_subpaths:
                    segmentation_datasets = f.list_datasets(path)
                    datasets[obj_name]['segmentation'].extend(
                        {
                            'path': '%s/%s' % (path, d),
                            'dtype': f.get_type('%s/%s' % (path, d))
                        }
                        for d in segmentation_datasets
                    )

    # Determine the dimensions of the final datasets,
    # i.e. the number of objects that were identified in each image
    # and the number of features that were measure per object.
    # The "metadata" datasets are simply scalars, all other datasets
    # have the same number of rows, which corresponds to the number of objects.
    logger.info('determine dimensions of the final datasets')
    n_jobs = len(input_files)
    n_objects = defaultdict(int)
    for i, filename in enumerate(input_files):

        with DatasetReader(filename) as f:

            for obj_name in object_names:
                features_path = 'objects/%s/features' % obj_name
                segmentation_path = 'objects/%s/segmentation' % obj_name
                if f.exists(features_path):
                    feat_path = datasets[obj_name]['features'][0]['path']
                    dims = f.get_dimensions(feat_path)
                elif f.exists(segmentation_path):
                    obj_ids_path = '%s/object_ids' % segmentation_path
                    dims = f.get_dimensions(obj_ids_path)
                else:
                    raise DataError('Features or segmentation data must exist.')
                n_objects[obj_name] += dims[0]

    # Create datasets on disk
    logger.info('preallocate final datasets')
    with DatasetWriter(output_file, 'w') as out:

        for d in datasets['metadata']:
            d.update({'dims': (n_jobs, )})
            out.preallocate(**d)

        for obj_name in object_names:
            for d in datasets[obj_name]['segmentation']:
                d.update({'dims': (n_objects[obj_name], )})
                out.preallocate(**d)
            for d in datasets[obj_name]['features']:
                d.update({'dims': (n_objects[obj_name], )})
                out.preallocate(**d)

    logger.info('load individual datasets and write data into final datasets')
    job_index_count = 0
    object_index_count = defaultdict(int)
    for obj_name in object_names:
        object_index_count[obj_name] = 0
    with DatasetWriter(output_file) as out:

        for i, filename in enumerate(input_files):

            logger.debug('process file: %s', filename)

            with DatasetReader(filename) as f:

                # Collect metadata per site, i.e. per job ID
                metadata_path = '/metadata'
                dataset_names = f.list_datasets(metadata_path)
                for name in dataset_names:
                    dataset_path = '{group}/{dataset}'.format(
                                        group=metadata_path,
                                        dataset=name)
                    data = f.read(dataset_path)
                    if len(data.shape) > 1:
                        raise DataError(
                                'Dataset must be one-dimensional: %s'
                                % dataset_path)
                    index = range(
                            job_index_count,
                            (1 + job_index_count)
                    )
                    out.write(dataset_path, data=data, index=index)
                job_index_count += 1

                # Collect features and segmentations per object
                for obj_name in object_names:
                    features_path = 'objects/%s/features' % obj_name
                    segmentation_path = 'objects/%s/segmentation' % obj_name

                    if f.exists(features_path):
                        dataset_names = f.list_datasets(features_path)
                        for name in dataset_names:
                            dataset_path = '{group}/{dataset}'.format(
                                            group=features_path,
                                            dataset=name)
                            data = f.read(dataset_path)
                            if len(data.shape) > 1:
                                raise DataError(
                                        'Dataset must be one-dimensional: %s'
                                        % dataset_path)
                            index = range(
                                    object_index_count[obj_name],
                                    (len(data) + object_index_count[obj_name])
                            )
                            out.write(dataset_path, data=data, index=index)

                    if f.exists(segmentation_path):
                        dataset_names = f.list_datasets(segmentation_path)
                        for name in dataset_names:
                            dataset_path = '{group}/{dataset}'.format(
                                            group=segmentation_path,
                                            dataset=name)
                            data = f.read(dataset_path)
                            if len(data.shape) > 1:
                                raise DataError(
                                        'Dataset must be one-dimensional: %s'
                                        % dataset_path)
                            index = range(
                                    object_index_count[obj_name],
                                    (len(data) + object_index_count[obj_name])
                            )
                            out.write(dataset_path, data=data, index=index)

                        group_names = f.list_groups(segmentation_path)
                        for g_name in group_names:
                            g_path = '{group}/{subgroup}'.format(
                                            group=segmentation_path,
                                            subgroup=g_name)
                            dataset_names = f.list_datasets(g_path)
                            for name in dataset_names:
                                dataset_path = '{group}/{dataset}'.format(
                                                group=g_path, dataset=name)
                                data = f.read(dataset_path)
                                if len(data.shape) > 1:
                                    raise DataError(
                                            'Dataset must be one-dimensional: %s'
                                            % dataset_path)
                                index = range(
                                        object_index_count[obj_name],
                                        (len(data) +
                                         object_index_count[obj_name])
                                )
                                out.write(dataset_path, data=data, index=index)

                    object_index_count[obj_name] += len(data)

            if delete_input_files:
                logger.debug('remove input file: %s', filename)
                os.remove(filename)


def update_datasets(old_filename, new_filename):
    '''
    Recursively copy all datasets from one HDF5 file to a new HDF5 file without
    overwriting existing ones.

    Parameters
    ----------
    old_filename: str
        absolute path to the file with new content
    new_filename: str
        absolute path to the file whose content should be updated

    Note
    ----
    Datesets related to visualization of objects in *TissueMAPS*, such as the
    map coordinates, are skipped. They should always be re-created, such that
    any changes can immediately be visualized.
    '''
    with DatasetReader(old_filename) as old_file:
        with DatasetWriter(new_filename) as new_file:
            def copy_recursive(p):
                groups = old_file.list_groups(p)
                for g in groups:
                    g_path = '{path}/{group}'.format(path=p, group=g)
                    if re.search(r'objects/[^/]+/map_data/', g_path):
                        logger.debug('skip group: %s', g_path)
                        continue
                    for d in old_file.list_datasets(g_path):
                        d_path = '{path}/{dataset}'.format(
                                    path=g_path, dataset=d)
                        if re.search(r'objects/[^/]+/ids', d_path):
                            logger.debug('skip dataset: %s', d_path)
                            continue
                        if not new_file.exists(d_path):
                            # Keep the more recent dataset.
                            logger.debug('copy dataset: %s', d_path)
                            new_file.write(d_path, data=old_file.read(d_path))
                    copy_recursive(g_path)
            copy_recursive('/')
