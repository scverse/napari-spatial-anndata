from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import numpy as np
from anndata import AnnData
from geopandas import GeoDataFrame
from loguru import logger
from napari import Viewer
from napari.layers import Image, Labels, Points, Shapes
from napari.layers.base import ActionType
from napari.utils.notifications import show_info
from shapely import Polygon
from spatialdata.models import PointsModel, ShapesModel
from spatialdata.transformations import Identity

from napari_spatialdata.utils._utils import (
    _adjust_channels_order,
    _get_transform,
    _swap_coordinates,
    get_duplicate_element_names,
)
from napari_spatialdata.utils._viewer_utils import _get_polygons_properties

if TYPE_CHECKING:
    import numpy.typing as npt
    from dask.dataframe.core import DataFrame as DaskDataFrame
    from napari.layers import Layer
    from napari.utils.events import Event, EventedList
    from spatialdata import SpatialData


class SpatialDataViewer:
    def __init__(self, viewer: Viewer, sdata: EventedList) -> None:
        self.viewer = viewer
        self.sdata = sdata
        self._layer_event_caches: dict[str, list[dict[str, Any]]] = {}
        self.viewer.bind_key("Shift-L", self._inherit_metadata)
        self.viewer.bind_key("Shift-S", self._save_to_sdata)
        self.viewer.layers.events.inserted.connect(self._on_layer_insert)
        self.viewer.layers.events.removed.connect(self._on_layer_removed)

        # Used to check old layer name. This because event emitted does not contain this information.
        self.layer_names: set[str | None] = set()

    def _on_layer_insert(self, event: Event) -> None:
        layer = event.value
        if layer.metadata.get("sdata"):
            self.layer_names.add(layer.name)
            self._layer_event_caches[layer.name] = []
            layer.events.data.connect(self._update_cache_indices)
            layer.events.name.connect(self._validate_name)

    def _on_layer_removed(self, event: Event) -> None:
        layer = event.value
        if layer.metadata["name"]:
            del self._layer_event_caches[layer.name]
            self.layer_names.remove(layer.name)

    def _validate_name(self, event: Event) -> None:
        _, element_names = get_duplicate_element_names(self.sdata)
        current_layer_names = [layer.name for layer in self.viewer.layers]
        old_layer_name = self.layer_names.difference(current_layer_names).pop()

        layer = event.source
        sdata = layer.metadata.get("sdata")

        pattern = r" \[\d+\]$"
        duplicate_pattern_found = re.search(pattern, layer.name)
        name_to_validate = re.sub(pattern, "", layer.name) if duplicate_pattern_found else layer.name

        # Ensures that the callback does not get called a second time when changing layer.name here.
        with layer.events.name.blocker(self._validate_name):
            if sdata:
                sdata_names = [element_name for _, element_name, _ in sdata._gen_elements()]
                if name_to_validate in sdata_names or duplicate_pattern_found:
                    layer.name = old_layer_name
                    show_info("New layer name causes name conflicts. Reverting to old layer name")
                elif name_to_validate in element_names:
                    sdata_index = self.sdata.index(sdata)
                    layer.name = name_to_validate + f"_{sdata_index}"
            elif duplicate_pattern_found or name_to_validate in element_names:
                layer.name = old_layer_name
                show_info("Layer name potentially causes naming conflicts with SpatialData elements. Reverting.")

        self.layer_names.remove(old_layer_name)
        self.layer_names.add(layer.name)

    def _update_cache_indices(self, event: Event) -> None:
        del event.value
        # This needs to be changed when we switch to napari 0.4.19
        if event.action == ActionType.REMOVE or (type(event.source) != Points and event.action == ActionType.CHANGE):
            # We overwrite the indices so they correspond to indices in the dataframe
            napari_indices = sorted(event.data_indices, reverse=True)
            event.indices = tuple(event.source.metadata["indices"][i] for i in napari_indices)
            if event.action == ActionType.REMOVE:
                for i in napari_indices:
                    del event.source.metadata["indices"][i]
        elif type(event.source) == Points and event.action == ActionType.CHANGE:
            logger.warning(
                "Moving events of Points in napari can't be cached due to a bug in napari 0.4.18. This will"
                "be available in napari 0.4.19"
            )
            return
        if event.action == ActionType.ADD:
            # we need to add based on the indices of the dataframe, which can be subsampled in case of points
            n_indices = event.source.metadata["_n_indices"]
            event.indices = tuple(n_indices + i for i in range(len(event.data_indices)))
            event.source.metadata["_n_indices"] = event.indices[-1] + 1
            event.source.metadata["indices"].extend(event.indices)

        layer_name = event.source.name
        self._layer_event_caches[layer_name].append(event)

    def _save_to_sdata(self, viewer: Viewer) -> None:
        layer_selection = list(viewer.layers.selection)
        self.save_to_sdata(layer_selection)

    def save_to_sdata(self, layers: list[Layer]) -> None:
        for layer in layers:
            if not layer.metadata["name"] and layer.metadata["sdata"]:
                sdata = layer.metadata["sdata"]
                coordinate_system = layer.metadata["_current_cs"]
                transformation = {coordinate_system: Identity()}
                swap_data: None | npt.ArrayLike
                if type(layer) == Points:
                    swap_data = np.fliplr(layer.data)
                    model = PointsModel.parse(swap_data, transformations=transformation)
                    sdata.points[layer.name] = model
                if type(layer) == Shapes:
                    if len(layer.data) == 0:
                        raise ValueError("Cannot save a shapes layer with no shapes")
                    polygons: list[Polygon] = [Polygon(i) for i in _swap_coordinates(layer.data)]
                    gdf = GeoDataFrame({"geometry": polygons})
                    model = ShapesModel.parse(gdf, transformations=transformation)
                    sdata.shapes[layer.name] = model
                    swap_data = None
                if type(layer) == Image or type(layer) == Labels:
                    raise NotImplementedError

                self.layer_names.add(layer.name)
                self._layer_event_caches[layer.name] = []
                self._update_metadata(layer, model, swap_data)
                layer.events.data.connect(self._update_cache_indices)
                layer.events.name.connect(self._validate_name)
            else:
                raise NotImplementedError("updating existing elements will soon be supported")

    def _update_metadata(self, layer: Layer, model: DaskDataFrame, data: None | npt.ArrayLike = None) -> None:
        layer.metadata["name"] = layer.name
        layer.metadata["_n_indices"] = len(layer.data)
        layer.metadata["indices"] = list(i for i in range(len(layer.data)))  # noqa: C400
        if type(layer) == Points:
            layer.metadata["adata"] = AnnData(obs=model, obsm={"spatial": data})

    def _inherit_metadata(self, viewer: Viewer) -> None:
        layers = list(viewer.layers.selection)
        self.inherit_metadata(layers)

    def inherit_metadata(self, layers: list[Layer]) -> None:
        """
        Inherit metadata from active layer.

        A new layer that is added will inherit from the layer that is active when its added, ensuring proper association
        with a spatialdata object and coordinate space.

        Parameters
        ----------
        layers: list[Layer]
            A list of napari layers of which only 1 should have a spatialdata object from which the other layers inherit
            metadata.
        """
        # Layer.metadata.get would yield a default value which is not what we want.
        sdatas = [layer.metadata["sdata"] for layer in layers if "sdata" in layer.metadata]

        # If more than 1 sdata object, ensure all are the same.
        if len(sdatas) > 1 and not all(sdatas[0] is sdata for sdata in sdatas[1:]):
            raise ValueError("Multiple different spatialdata object found in selected layers. One is required.")

        if len(sdatas) < 1:
            raise ValueError("No Spatialdata objects associated with selected layers.")

        ref_layer = next(layer for layer in layers if "sdata" in layer.metadata)

        for layer in (
            layer
            for layer in layers
            if layer != ref_layer and isinstance(layer, (Labels, Points, Shapes)) and "sdata" not in layer.metadata
        ):
            layer.metadata["sdata"] = ref_layer.metadata["sdata"]
            layer.metadata["_current_cs"] = ref_layer.metadata["_current_cs"]
            layer.metadata["_active_in_cs"] = {ref_layer.metadata["_current_cs"]}
            layer.metadata["name"] = None
            layer.metadata["adata"] = None
            if isinstance(layer, (Shapes, Labels)):
                layer.metadata["region_key"] = None
            if isinstance(layer, (Shapes, Points)):
                layer.metadata["_n_indices"] = None
                layer.metadata["indices"] = None

        show_info(f"Layer(s) without associated SpatialData object inherited SpatialData metadata of {ref_layer}")

    def add_sdata_image(self, sdata: SpatialData, key: str, selected_cs: str, multi: bool) -> None:
        original_name = key
        if multi:
            original_name = original_name[: original_name.rfind("_")]

        affine = _get_transform(sdata.images[original_name], selected_cs)
        rgb_image, rgb = _adjust_channels_order(element=sdata.images[original_name])

        # TODO: type check
        self.viewer.add_image(
            rgb_image,
            rgb=rgb,
            name=key,
            affine=affine,
            metadata={
                "sdata": sdata,
                "name": original_name,
                "_active_in_cs": {selected_cs},
                "_current_cs": selected_cs,
            },
        )

    def add_sdata_circles(self, sdata: SpatialData, key: str, selected_cs: str, multi: bool) -> None:
        original_name = key
        if multi:
            original_name = original_name[: original_name.rfind("_")]

        df = sdata.shapes[original_name]
        affine = _get_transform(sdata.shapes[original_name], selected_cs)

        xy = np.array([df.geometry.x, df.geometry.y]).T
        xy = np.fliplr(xy)
        radii = df.radius.to_numpy()

        self.viewer.add_points(
            xy,
            name=key,
            affine=affine,
            size=2 * radii,
            edge_width=0.0,
            metadata={
                "sdata": sdata,
                "adata": sdata.table[
                    sdata.table.obs[sdata.table.uns["spatialdata_attrs"]["region_key"]] == original_name
                ],
                "region_key": sdata.table.uns["spatialdata_attrs"]["region_key"],
                "name": original_name,
                "_active_in_cs": {selected_cs},
                "_current_cs": selected_cs,
                "_n_indices": len(df),
                "indices": df.index.to_list(),
            },
        )

    def add_sdata_shapes(self, sdata: SpatialData, key: str, selected_cs: str, multi: bool) -> None:
        original_name = key
        if multi:
            original_name = original_name[: original_name.rfind("_")]

        df = sdata.shapes[original_name]
        affine = _get_transform(sdata.shapes[original_name], selected_cs)

        # when mulitpolygons are present, we select the largest ones
        if "MultiPolygon" in np.unique(df.geometry.type):
            logger.info("Multipolygons are present in the data. Only the largest polygon per cell is retained.")
            df = df.explode(index_parts=False)
            df["area"] = df.area
            df = df.sort_values(by="area", ascending=False)  # sort by area
            df = df[~df.index.duplicated(keep="first")]  # only keep the largest area
            df = df.sort_index()  # reset the index to the first order

        simplify = len(df) > 100
        polygons, indices = _get_polygons_properties(df, simplify)

        # this will only work for polygons and not for multipolygons
        polygons = _swap_coordinates(polygons)

        self.viewer.add_shapes(
            polygons,
            name=key,
            affine=affine,
            shape_type="polygon",
            metadata={
                "sdata": sdata,
                "adata": sdata.table[sdata.table.obs[sdata.table.uns["spatialdata_attrs"]["region_key"]] == key],
                "region_key": sdata.table.uns["spatialdata_attrs"]["region_key"],
                "name": original_name,
                "_active_in_cs": {selected_cs},
                "_current_cs": selected_cs,
                "_n_indices": len(df),
                "indices": indices,
            },
        )

    def add_sdata_labels(self, sdata: SpatialData, key: str, selected_cs: str, multi: bool) -> None:
        original_name = key
        if multi:
            original_name = original_name[: original_name.rfind("_")]

        affine = _get_transform(sdata.labels[original_name], selected_cs)
        rgb_labels, _ = _adjust_channels_order(element=sdata.labels[original_name])

        self.viewer.add_labels(
            rgb_labels,
            name=key,
            affine=affine,
            metadata={
                "sdata": sdata,
                "adata": sdata.table[sdata.table.obs[sdata.table.uns["spatialdata_attrs"]["region_key"]] == key],
                "region_key": sdata.table.uns["spatialdata_attrs"]["instance_key"],
                "name": original_name,
                "_active_in_cs": {selected_cs},
                "_current_cs": selected_cs,
            },
        )

    def add_sdata_points(self, sdata: SpatialData, key: str, selected_cs: str, multi: bool) -> None:
        original_name = key
        if multi:
            original_name = original_name[: original_name.rfind("_")]

        points = sdata.points[original_name].compute()
        affine = _get_transform(sdata.points[original_name], selected_cs)
        if len(points) < 100000:
            subsample = np.arange(len(points))
        else:
            logger.info("Subsampling points because the number of points exceeds the currently supported 100 000.")
            gen = np.random.default_rng()
            subsample = np.sort(gen.choice(len(points), size=100000, replace=False))  # same as indices

        xy = points[["y", "x"]].values[subsample]
        np.fliplr(xy)
        self.viewer.add_points(
            xy,
            name=key,
            size=20,
            affine=affine,
            edge_width=0.0,
            metadata={
                "sdata": sdata,
                "adata": AnnData(obs=points.iloc[subsample, :], obsm={"spatial": xy}),
                "name": original_name,
                "_active_in_cs": {selected_cs},
                "_current_cs": selected_cs,
                "_n_indices": len(points),
                "indices": subsample.tolist(),
            },
        )

    def _affine_transform_layers(self, coordinate_system: str) -> None:
        for layer in self.viewer.layers:
            metadata = layer.metadata
            if metadata.get("sdata"):
                sdata = metadata["sdata"]
                element_name = metadata["name"]
                element_data = sdata[element_name]
                affine = _get_transform(element_data, coordinate_system)
                if affine is not None:
                    layer.affine = affine
