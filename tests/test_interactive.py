import pytest
from spatialdata import SpatialData
from spatialdata.models import Image2DModel

from napari_spatialdata._interactive import Interactive
from tests.conftest import PlotTester, PlotTesterMeta


class TestImages(PlotTester, metaclass=PlotTesterMeta):
    def test_plot_can_add_element_image(self, sdata_blobs: SpatialData):
        blobs_image = Image2DModel.parse(sdata_blobs["blobs_image"], c_coords=("r", "g", "b"))
        sdata_blobs["blobs_image"] = blobs_image
        i = Interactive(sdata=sdata_blobs, headless=True)
        i.add_element(element="blobs_image", element_coordinate_system="global")

    def test_plot_can_add_element_label(self, sdata_blobs: SpatialData):
        i = Interactive(sdata=sdata_blobs, headless=True)
        i.add_element(element="blobs_labels", element_coordinate_system="global")

    def test_plot_can_add_element_multiple(self, sdata_blobs: SpatialData):
        blobs_image = Image2DModel.parse(sdata_blobs["blobs_image"], c_coords=("r", "g", "b"))
        sdata_blobs["blobs_image"] = blobs_image
        i = Interactive(sdata=sdata_blobs, headless=True)
        i.add_element(element="blobs_image", element_coordinate_system="global")
        i.add_element(element="blobs_labels", element_coordinate_system="global")
        i.add_element(element="blobs_circles", element_coordinate_system="global")
        assert not i._sdata_widget.coordinate_system_widget._system
        assert not i._sdata_widget.elements_widget._elements
        for layer in i._viewer.layers:
            assert layer.visible

    def test_switch_coordinate_system(self, sdata_blobs: SpatialData):
        i = Interactive(sdata=sdata_blobs, headless=True)
        assert not i._sdata_widget.coordinate_system_widget._system
        assert not i._sdata_widget.elements_widget._elements
        i.switch_coordinate_system("global")
        assert i._sdata_widget.coordinate_system_widget._system == "global"
        assert i._sdata_widget.elements_widget._elements
        i._viewer.close()


def test_plot_can_add_element_switch_cs(sdata_blobs: SpatialData):
    i = Interactive(sdata=sdata_blobs, headless=True)
    i.add_element(element="blobs_image", element_coordinate_system="global", view_element_system=True)
    assert i._sdata_widget.coordinate_system_widget._system == "global"
    assert i._viewer.layers[-1].visible
    i._viewer.close()


class TestInteractive(PlotTester, metaclass=PlotTesterMeta):
    def test_get_layer_existing(self, sdata_blobs: SpatialData):
        i = Interactive(sdata=sdata_blobs, headless=True)
        i.add_element(element="blobs_image", element_coordinate_system="global")
        layer = i.get_layer("blobs_image")
        assert layer is not None, "Expected to retrieve the blobs_image layer, but got None"
        assert layer.name == "blobs_image", f"Expected layer name 'blobs_image', got {layer.name}"
        i._viewer.close()

    def test_get_layer_non_existing(self, sdata_blobs: SpatialData):
        i = Interactive(sdata=sdata_blobs, headless=True)
        layer = i.get_layer("non_existing_layer")
        assert layer is None, "Expected None for a non-existing layer, but got a layer"
        i._viewer.close()

    def test_add_text_to_polygons(self, sdata_polygons: SpatialData):
        i = Interactive(sdata=sdata_polygons, headless=True)
        i.add_element(element="polygons", element_coordinate_system="global")

        # Mock polygon layer with some polygon data
        text_annotations = ["Label 1", "Label 2", "Label 3"]
        polygon_layer = i.get_layer("polygons")

        # Verify that text is added correctly
        i.add_text_to_polygons(layer_name="polygons", text_annotations=text_annotations)
        assert polygon_layer.text is not None, "Text annotations were not added to the polygon layer"
        assert polygon_layer.text["string"] == text_annotations, "Text annotations do not match"
        i._viewer.close()

    def test_add_text_to_polygons_mismatched_annotations(self, sdata_polygons: SpatialData):
        i = Interactive(sdata=sdata_polygons, headless=True)
        i.add_element(element="polygons", element_coordinate_system="global")

        # Mock polygon layer with some polygon data
        text_annotations = ["Label 1", "Label 2"]

        # Expect ValueError due to mismatch between polygons and text annotations
        with pytest.raises(ValueError, match="The number of text annotations must match the number of polygons"):
            i.add_text_to_polygons(layer_name="polygons", text_annotations=text_annotations)
        i._viewer.close()

    def test_add_text_to_polygons_non_existing_layer(self, sdata_polygons: SpatialData):
        i = Interactive(sdata=sdata_polygons, headless=True)

        # Mock non-existing layer
        text_annotations = ["Label 1", "Label 2", "Label 3"]

        # Expect ValueError due to non-existing layer
        with pytest.raises(ValueError, match="Polygon layer 'non_existing_layer' not found"):
            i.add_text_to_polygons(layer_name="non_existing_layer", text_annotations=text_annotations)
        i._viewer.close()
