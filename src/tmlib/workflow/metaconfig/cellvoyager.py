import re
import logging
import bioformats
from collections import defaultdict
from lxml import etree

from tmlib import utils
from tmlib.workflow.illuminati import stitch
from tmlib.workflow.metaconfig.base import MetadataReader
from tmlib.workflow.metaconfig.base import MetadataHandler
from tmlib.workflow.metaconfig.omexml import XML_DECLARATION

logger = logging.getLogger(__name__)

#: Regular expression pattern to identify image files
IMAGE_FILE_REGEX_PATTERN = '[^_]+_(?P<w>[A-Z]\d{2})_T(?P<t>\d+)F(?P<s>\d+)L\d+A\d+Z(?P<z>\d+)C(?P<c>\d+)\.'

#: Supported extensions for metadata files
METADATA_FILE_REGEX_PATTERN = '\.(mlf|mrf)$'


class CellvoyagerMetadataHandler(MetadataHandler):

    '''Class for handling metadata specific to the Yokogawa Cellvoyager 7000
    microscope.
    '''

    #: Regular expression pattern to identify image files
    IMAGE_FILE_REGEX_PATTERN = IMAGE_FILE_REGEX_PATTERN

    def __init__(self, omexml_images, omexml_metadata=None):
        '''
        Parameters
        ----------
        omexml_images: Dict[str, bioformats.omexml.OMEXML]
            metadata extracted from microscope image files
        omexml_metadata: bioformats.omexml.OMEXML
            metadata extracted from microscope metadata files 
        '''
        super(CellvoyagerMetadataHandler, self).__init__(
            omexml_images, omexml_metadata
        )

    @staticmethod
    def _calculate_coordinates(positions):
        # y axis is inverted
        coordinates = stitch.calc_grid_coordinates_from_positions(
            positions, reverse_rows=True
        )
        return coordinates


class CellvoyagerMetadataReader(MetadataReader):

    '''Class for reading metadata from files formats specific to the Yokogawa
    CellVoyager 7000 microscope.

    Yokogawa doesn't store the position and channel information at the level
    of individual images, but in additional metadata files with *.mlf* and
    *.mrf* extensions.
    Unfortunately, these file formats are not supported by Bio-Formats,
    see `CellVoyagerReader <http://www.openmicroscopy.org/site/support/bio-formats5.1/formats/cellvoyager.html>`_.
    For compatibility with the OME data model, the `CellvoyagerMetadataReader`
    reads the XML from "MeasurementDetail.mrf" and "MeasurmentData.mlf" files,
    extracts the relevant data and stores them in an OMEXML object according to
    the Bio-Formats convention.
    '''

    def read(self, microscope_metadata_files, microscope_image_files):
        '''Read metadata from "mlf" and "mrf" metadata files.

        Parameters
        ----------
        microscope_metadata_files: List[str]
            absolute path to the microscope metadata files
        microscope_image_files: List[str]
            absolute path to the microscope image files

        Returns
        -------
        bioformats.omexml.OMEXML
            OMEXML image metadata

        Raises
        ------
        ValueError
            when `microscope_metadata_files` doesn't have length two
        '''
        if len(microscope_metadata_files) != 2:
            raise ValueError('Expected two microscope metadata files.')
        for f in microscope_metadata_files:
            if f.endswith('mlf'):
                mlf_filename = f
            elif f.endswith('mrf'):
                mrf_filename = f
        metadata = bioformats.OMEXML(XML_DECLARATION)
        # Obtain the positional information for each image acquisition site
        # from the ".mlf" file:
        mlf_tree = etree.parse(mlf_filename)
        mlf_root = mlf_tree.getroot()
        mlf_elements = mlf_root.xpath(
            './/bts:MeasurementRecord', namespaces=mlf_root.nsmap
        )
        mlf_ns = mlf_root.nsmap['bts']

        metadata.image_count = len(mlf_elements)
        lookup = defaultdict(list)
        r = re.compile(CellvoyagerMetadataHandler.IMAGE_FILE_REGEX_PATTERN)

        y = list()
        x = list()
        for i, e in enumerate(mlf_elements):
            img = metadata.image(i)
            # A name has to be set as a flag for the handler to update
            # the metadata
            img.Name = e.text
            # TODO: there is a bug that prevents setting the date for
            # images with index > 0
            img.AcquisitionDate = e.attrib['{%s}Time' % mlf_ns]
            # Image files always contain only a single plane
            img.Pixels.SizeT = 1
            img.Pixels.SizeC = 1
            img.Pixels.SizeZ = 1
            img.Pixels.plane_count = 1
            if e.attrib['{%s}Type' % mlf_ns] == 'IMG':
                img.Pixels.Channel(0).Name = e.attrib['{%s}Ch' % mlf_ns]
            else:
                logger.error(
                    'erroneous acquisition - no channel information '
                    'available for image "%s"', img.Name
                )
                img.Pixels.Channel(0).Name = None
            img.Pixels.Plane(0).PositionX = float(e.attrib['{%s}X' % mlf_ns])
            img.Pixels.Plane(0).PositionY = float(e.attrib['{%s}Y' % mlf_ns])

            x.append(img.Pixels.Plane(0).PositionX)
            y.append(img.Pixels.Plane(0).PositionY)

            matches = r.search(img.Name)
            # NOTE: We use a dictionary as reference, which is not serializable
            # into XML.
            captures = matches.groupdict()
            well_row = utils.map_number_to_letter(
                            int(e.attrib['{%s}Row' % mlf_ns]))
            well_col = int(e.attrib['{%s}Column' % mlf_ns])
            well_id = '%s%.2d' % (well_row, well_col)
            lookup[well_id].append(captures)

        # Obtain the general experiment information and well plate format
        # specifications from the ".mrf" file:
        mrf_tree = etree.parse(mrf_filename)
        mrf_root = mrf_tree.getroot()
        mrf_ns = mrf_root.nsmap['bts']
        e = mrf_root
        name = e.attrib['{%s}Title' % mrf_ns]
        plate = metadata.PlatesDucktype(metadata.root_node).newPlate(name=name)
        plate.RowNamingConvention = 'letter'
        plate.ColumnNamingConvention = 'number'
        plate.Rows = e.attrib['{%s}RowCount' % mrf_ns]
        plate.Columns = e.attrib['{%s}ColumnCount' % mrf_ns]
        wells = lookup.keys()
        for w in set(wells):
            # Create a *Well* element for each imaged well in the plate
            row = utils.map_letter_to_number(w[0]) - 1
            col = int(w[1:]) - 1
            well = metadata.WellsDucktype(plate).new(row=row, column=col)
            well_samples = metadata.WellSampleDucktype(well.node)
            for i, reference in enumerate(lookup[w]):
                # Create a *WellSample* element for each acquisition site
                well_samples.new(index=i)
                well_samples[i].ImageRef = reference

        return metadata