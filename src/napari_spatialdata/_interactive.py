from typing import List

from spatialdata import SpatialData
from napari.viewer import Viewer
from qtpy.QtWidgets import QLabel, QWidget, QListWidget, QVBoxLayout
import napari


class SdataWidget(QWidget):
    def __init__(self, viewer: Viewer, sdata: SpatialData):
        super().__init__()
        self._sdata = sdata
        self._viewer = viewer
        self._points = QListWidget()
        self._labels = QListWidget()
        self._images = QListWidget()
        self.setLayout(QVBoxLayout())
        self.layout().addWidget(QLabel("Labels:"))
        self.layout().addWidget(self._labels)
        self.layout().addWidget(QLabel("Images:"))
        self.layout().addWidget(self._images)
        self.layout().addWidget(QLabel("Points:"))
        self.layout().addWidget(self._points)
        self._labels.addItems(self.get_formatted_keys("labels_"))
        self._images.addItems(self.get_formatted_keys("images_"))
        self._points.addItems(self.get_formatted_keys("points_"))
        self._labels.itemDoubleClicked.connect(lambda item: self._onClick(item.text()))
        self._images.itemDoubleClicked.connect(lambda item: self._onClick(item.text()))
        self._points.itemDoubleClicked.connect(lambda item: self._onClick(item.text()))

    def _onClick(self, text: str) -> None:
        key = text[text.find("_") + 1 :]  # Only take string after "_" e.g labels_3 becomes 3

        if "labels" in text:
            self._add_label(key)
        elif "images" in text:
            self._add_image(key)
        elif "points" in text:
            raise NotImplementedError("Points is currently not supported due to performance issues!")

    def get_formatted_keys(self, string_to_append: str) -> List[str]:
        formatted_keys = []
        for key in sdata.labels.keys():  # Labels, points and images have the same keys
            formatted_keys.append(string_to_append + key)

        return formatted_keys

    def _add_label(self, key: str) -> None:
        self._viewer.add_labels(
            self._sdata.labels[key],
            name="labels_" + key,
            metadata={
                "adata": self._sdata.table[
                    self._sdata.table.obs[sdata.table.uns["spatialdata_attrs"]["region_key"]] == key
                ],
                "labels_key": self._sdata.table.uns["spatialdata_attrs"]["instance_key"],
            },
        )

    def _add_image(self, key: str) -> None:
        self._viewer.add_image(
            self._sdata.images[key],
            name="images_" + key,
            metadata={
                "adata": self._sdata.table[
                    self._sdata.table.obs[sdata.table.uns["spatialdata_attrs"]["region_key"]] == key
                ],
                "labels_key": self._sdata.table.uns["spatialdata_attrs"]["instance_key"],
            },
        )

    def _add_point(self, key: str) -> None:
        self._viewer.add_points(
            self._sdata.points[key],
            name=key,
        )

        # TODO magicgui update in plugins --possible?


class Interactive:
    def __init__(self, sdata: SpatialData):
        self._viewer = napari.Viewer()
        self._sdata = sdata
        self._sdata_widget = SdataWidget(self._viewer, sdata)
        self._list_widget = self._viewer.window.add_dock_widget(
            self._sdata_widget, name="Select spatialdata segment", area="left"
        )
        # TODO add to window

    def run(self) -> None:
        napari.run()


if __name__ == "__main__":  # TODO: create example instead of this
    sdata = SpatialData.read("../cosmx.zarr")
    sdata.table.uns["spatialdata_attrs"]["region"] = 0
    i = Interactive(sdata)
    i.run()
