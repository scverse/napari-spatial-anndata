from __future__ import annotations

from typing import Any, Union, Iterable, Optional, TYPE_CHECKING

from qtpy import QtWidgets
from loguru import logger
from qtpy.QtCore import Signal
from napari.viewer import Viewer
from matplotlib.path import Path
from matplotlib.widgets import LassoSelector
from napari_matplotlib.base import NapariMPLWidget
import numpy as np
import napari
from napari.layers import Image, Layer, Labels, Points
import pandas as pd

from napari_spatialdata._model import ImageModel
from napari_spatialdata._utils import NDArrayA, _set_palette, _get_categorical
from napari_spatialdata._widgets import AListWidget, ComponentWidget
from napari_spatialdata._categoricals_utils import _add_categorical_legend

__all__ = [
    "MatplotlibWidget",
    "AxisWidgets",
]


class SelectFromCollection:
    """
    Select indices from a matplotlib collection using `LassoSelector`.

    Selected indices are saved in the `ind` attribute. This tool fades out the
    points that are not part of the selection (i.e., reduces their alpha
    values). If your collection has alpha < 1, this tool will permanently
    alter the alpha values.

    Note that this tool selects collection objects based on their *origins*
    (i.e., `offsets`).

    Parameters
    ----------
    ax : `~matplotlib.axes.Axes`
        Axes to interact with.
    collection : `matplotlib.collections.Collection` subclass
        Collection you want to select from.
    alpha_other : 0 <= float <= 1
        To highlight a selection, this tool sets all selected points to an
        alpha value of 1 and non-selected points to *alpha_other*.
    """

    def __init__(self, viewer: Viewer, model: ImageModel, ax, collection, data, alpha_other=0.3):
        self.viewer = viewer
        self.model = model 
        self.canvas = ax.figure.canvas
        self.collection = collection
        self.alpha_other = alpha_other
        self.exported_data = None
        self.data = data
        self.axes = ax

        self.xys = collection.get_offsets()
        self.Npts = len(self.xys)

        # Ensure that we have separate colors for each object
        self.fc = collection.get_facecolors()
        logger.debug("Facecolors: {}", self.fc)
        if len(self.fc) == 0:
            raise ValueError("Collection must have a facecolor")
        elif len(self.fc) == 1:
            self.fc = np.tile(self.fc, (self.Npts, 1))
            
        self.selector = LassoSelector(ax, onselect=self.onselect)
        
        self.ind = []

    def export(self, adata) -> None:
        adata.obs["0_LASSO_SELECTED"] = self.exported_data
        
    def onselect(self, verts) -> None:

        path = Path(verts)
        self.ind = np.nonzero(path.contains_points(self.xys))[0]

        self.fc[:, -1] = self.alpha_other
        self.fc[self.ind, -1] = 1

        # logger.debug("FINAL FC 1: {}", self.fc)
        
        # self.fc[(self.fc > 0.1)] = 1
        # logger.debug("FINAL FC 2: {}", self.fc)
        
        self.collection.set_facecolors(self.fc)
        
        self.canvas.draw_idle()
        
        # self.axes.clear()

        # logger.debug("Scatterplot colors: {}", self.data[2])
        # logger.debug("Type of scatterplot colors: {}", type(self.data[2]))

        # self.data[2][(self.data[2] > 0)] = 1
        # self.scatterplot = self.axes.scatter(x=self.data[0], y=self.data[1], c=self.data[2])
        # self.axes.set_xlabel("Test label")

        # print("SHAPE: ", self.data[2].shape)

        self.selected_coordinates = self.xys[self.ind].data        
        self.exported_data = pd.Categorical(path.contains_points(self.xys))
        
    def disconnect(self) -> None:
        self.selector.disconnect_events()
        self.poly.disconnect_events()
        self.fc[:, -1] = 1
        self.collection.set_facecolors(self.fc)
        self.canvas.draw_idle()
 

class ScatterListWidget(AListWidget):
    attrChanged = Signal()
    _text = None
    _chosen = None

    def __init__(self, viewer: Viewer, model: ImageModel, attr: str, color: bool, **kwargs: Any):
        AListWidget.__init__(self, viewer, model, attr, **kwargs)
        self.attrChanged.connect(self._onChange)
        self._color = color
        self._data = None
        self.itemClicked.connect(lambda item: self._onOneClick((item.text(),)))

    def _onChange(self) -> None:
        AListWidget._onChange(self)
        self.data = None
        self.text = None
        self.chosen = None

    def _onAction(self, items: Iterable[str]) -> None:
        for item in sorted(set(items)):
            try:
                vec, _ = self._getter(item, index=self.getIndex())
            except Exception as e:  # noqa: B902
                logger.error(e)
                continue
            self.chosen = item
            if isinstance(vec, np.ndarray):
                self.data = vec
            elif vec.dtype == "category":
                self.data = vec
                colortypes = _set_palette(self.model.adata, key=item, palette=self.model.palette, vec=self.data)
                if self._color:
                    self.data = {
                        "vec": _get_categorical(self.model.adata, key=item, palette=self.model.palette, vec=colortypes),
                        "cat": self.data,
                        "palette": colortypes,
                    }
            else:
                raise TypeError(f"The chosen field's datatype ({vec.dtype.name}) cannot be plotted")
        return

    def _onOneClick(self, items: Iterable[str]) -> None:
        if self.getAttribute() == "obsm":
            return
        self._onAction(items)
        return

    def setAttribute(self, field: Optional[str]) -> None:
        if field == self.getAttribute():
            return
        if field not in ("var", "obs", "obsm"):
            raise ValueError(f"{field} is not a valid adata field.")
        self._attr = field
        self._getter = getattr(self.model, f"get_{field}")
        self.attrChanged.emit()

    def getAttribute(self) -> Optional[str]:
        if TYPE_CHECKING:
            assert isinstance(self._attr, str)
        return self._attr

    def setComponent(self, text: Optional[Union[int, str]]) -> None:

        if self.getAttribute() == "var":
            if TYPE_CHECKING:
                assert isinstance(text, str)
            self.text = text
            super().setAdataLayer(text)
        elif self.getAttribute() == "obsm":
            if TYPE_CHECKING:
                assert isinstance(text, int) or isinstance(text, str)
            self.text = text  # type: ignore[assignment]
            super().setIndex(text)

    @property
    def text(self) -> Optional[str]:
        return self._text

    @text.setter
    def text(self, text: Optional[Union[str, int]]) -> None:
        self._text = text if text is not None else None

    @property
    def chosen(self) -> Optional[str]:
        return self._chosen

    @chosen.setter
    def chosen(self, chosen: Optional[str]) -> None:
        self._chosen = chosen if chosen is not None else None

    @property
    def data(self) -> Union[None, NDArrayA]:
        return self._data

    @data.setter
    def data(self, data: NDArrayA) -> None:
        self._data = data


class MatplotlibWidget(NapariMPLWidget):
    def __init__(self, viewer: Viewer, model: ImageModel):

        super().__init__(viewer)

        self._viewer = viewer
        self._model = model
        self.axes = self.canvas.figure.subplots()
        self.colorbar = None
        self.selector = None

    def _onClick(
        self,
        x_data: Union[NDArrayA, pd.Series],
        y_data: Union[NDArrayA, pd.Series],
        color_data: Union[NDArrayA, dict[str, Union[NDArrayA, pd.Series, dict[str, str]]]],
        x_label: Optional[str],
        y_label: Optional[str],
        color_label: Optional[str],
    ) -> None:

        self.cat = None
        self.palette = None

        if isinstance(color_data, dict):
            self.data = [x_data, y_data, color_data["vec"]]
            self.cat = color_data["cat"]
            self.palette = color_data["palette"]
        else:
            self.data = [x_data, y_data, color_data]
        self.x_label = x_label
        self.y_label = y_label
        self.color_label = color_label

        self.plot()

    def plot(self) -> None:

        logger.info("Plotting coordinates.")

        self.clear()

        self.scatterplot = self.axes.scatter(x=self.data[0], y=self.data[1], c=self.data[2])
        self.axes.set_xlabel(self.x_label)
        self.axes.set_ylabel(self.y_label)

        if self.palette is not None:
            _add_categorical_legend(
                self.axes,
                self.cat,
                palette=self.palette,
            )
            self.colorbar = None
        else:
            self.colorbar = self.canvas.figure.colorbar(self.scatterplot)
            if self.colorbar is None:
                raise ValueError("Colorbar hasn't been created.")
            self.colorbar.set_label(self.color_label)

        self.canvas.draw()

        self.selector = SelectFromCollection(self._viewer, self._model, self.axes, self.scatterplot, self.data)
        
    def clear(self) -> None:
        
        if self.colorbar:
            self.colorbar.remove()
            self.colorbar = None
            
        self.axes.clear()

        


class AxisWidgets(QtWidgets.QWidget):
    def __init__(self, viewer: Viewer, model: ImageModel, name: str, color: bool = False):

        super().__init__()

        self._viewer = viewer
        self._model = model

        selection_label = QtWidgets.QLabel(f"{name} type:")
        selection_label.setToolTip("Select between obs, obsm and var.")
        self.selection_widget = QtWidgets.QComboBox()
        self.selection_widget.addItem("obsm", None)
        self.selection_widget.addItem("obs", None)
        self.selection_widget.addItem("var", None)

        self.setLayout(QtWidgets.QVBoxLayout())
        self.layout().addWidget(selection_label)
        self.layout().addWidget(self.selection_widget)

        label = QtWidgets.QLabel(f"Select for {name}:")
        label.setToolTip(f"Select {name}.")

        self.widget = ScatterListWidget(self.viewer, self.model, attr="obsm", color=color)
        self.widget.setAttribute("obsm")

        self.component_widget = ComponentWidget(self.model, attr="obsm")
        self.component_widget.setToolTip("obsm")
        self.component_widget.currentTextChanged.connect(self.widget.setComponent)
        self.widget.itemClicked.connect(self.component_widget._onClickChange)

        self.layout().addWidget(label)
        self.layout().addWidget(self.widget)
        self.layout().addWidget(self.component_widget)

        self.selection_widget.currentTextChanged.connect(self.widget.setAttribute)
        self.selection_widget.currentTextChanged.connect(self.component_widget.setAttribute)
        self.selection_widget.currentTextChanged.connect(self.component_widget.setToolTip)

    def getFormattedLabel(self) -> Optional[str]:

        return (
            str(self.widget.getAttribute()) + ": " + str(self.widget.chosen)
            if self.widget.text is None
            else (
                str(self.widget.getAttribute()) + ": " + str(self.widget.chosen) + "[" + str(self.widget.text) + "]"
                if self.widget.getAttribute() == "obsm"
                else str(self.widget.getAttribute())
                + ": "
                + str(self.widget.chosen)
                + " on layer "
                + str(self.widget.text)
            )
        )

    @property
    def viewer(self) -> napari.Viewer:
        """:mod:`napari` viewer."""
        return self._viewer

    @property
    def model(self) -> ImageModel:
        """:mod:`napari` viewer."""
        return self._model
