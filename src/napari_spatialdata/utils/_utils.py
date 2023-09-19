from __future__ import annotations

from collections import Counter
from functools import wraps
from typing import TYPE_CHECKING, Any, Callable, Iterable, Optional, Sequence, Union

import numpy as np
import pandas as pd
from anndata import AnnData
from dask.dataframe.core import DataFrame as DaskDataFrame
from datatree import DataTree
from geopandas import GeoDataFrame
from loguru import logger
from matplotlib.colors import is_color_like, to_rgb
from multiscale_spatial_image.multiscale_spatial_image import MultiscaleSpatialImage
from numba import njit, prange
from pandas.api.types import infer_dtype, is_categorical_dtype
from pandas.core.dtypes.common import (
    is_bool_dtype,
    is_integer_dtype,
    is_numeric_dtype,
    is_object_dtype,
    is_string_dtype,
)
from qtpy.QtWidgets import QListWidgetItem
from scipy.sparse import issparse, spmatrix
from scipy.spatial import KDTree
from spatial_image import SpatialImage
from spatialdata import SpatialData
from spatialdata.models import SpatialElement, get_axes_names
from spatialdata.transformations import get_transformation

from napari_spatialdata._constants._pkg_constants import Key
from napari_spatialdata.utils._categoricals_utils import (
    add_colors_for_categorical_sample_annotation,
)

if TYPE_CHECKING:
    from napari.utils.events import EventedList
    from xarray import DataArray

try:
    from numpy.typing import NDArray

    NDArrayA = NDArray[Any]
except (ImportError, TypeError):
    NDArray = np.ndarray  # type: ignore[misc]
    NDArrayA = np.ndarray  # type: ignore[misc]


Vector_name_t = tuple[Optional[Union[pd.Series, NDArrayA]], Optional[str]]


def _ensure_dense_vector(fn: Callable[..., Vector_name_t]) -> Callable[..., Vector_name_t]:
    @wraps(fn)
    def decorator(self: Any, *args: Any, **kwargs: Any) -> Vector_name_t:
        normalize = kwargs.pop("normalize", False)
        res, fmt = fn(self, *args, **kwargs)
        if res is None:
            return None, None

        if isinstance(res, pd.Series):
            if is_categorical_dtype(res):
                return res, fmt
            if is_string_dtype(res) or is_object_dtype(res) or is_bool_dtype(res):
                return res.astype("category"), fmt
            if is_integer_dtype(res):
                unique = res.unique()
                n_uniq = len(unique)
                if n_uniq <= 2 and (set(unique) & {0, 1}):
                    return res.astype(bool).astype("category"), fmt
                if len(unique) <= len(res) // 100:
                    return res.astype("category"), fmt
            elif not is_numeric_dtype(res):
                raise TypeError(f"Unable to process `pandas.Series` of type `{infer_dtype(res)}`.")
            res = res.to_numpy()
        elif issparse(res):
            if TYPE_CHECKING:
                assert isinstance(res, spmatrix)
            res = res.toarray()
        elif not isinstance(res, (np.ndarray, Sequence)):
            raise TypeError(f"Unable to process result of type `{type(res).__name__}`.")

        res = np.asarray(np.squeeze(res))
        if res.ndim != 1:
            raise ValueError(f"Expected 1-dimensional array, found `{res.ndim}`.")

        return (_min_max_norm(res) if normalize else res), fmt

    return decorator


def _get_palette(
    adata: AnnData,
    key: str,
    palette: str | None = None,
    vec: pd.Series | None = None,
) -> dict[Any, Any]:
    if key not in adata.obs:
        raise KeyError("Missing key!")  # TODO: Improve error message

    return dict(zip(adata.obs[key].cat.categories, [to_rgb(i) for i in adata.uns[Key.uns.colors(key)]]))


def _set_palette(
    adata: AnnData,
    key: str,
    palette: str | None = None,
    vec: pd.Series | None = None,
) -> dict[Any, Any]:
    if vec is not None and not is_categorical_dtype(vec):
        raise TypeError(f"Expected a `categorical` type, found `{infer_dtype(vec)}`.")

    add_colors_for_categorical_sample_annotation(
        adata,
        key=key,
        vec=vec,
        force_update_colors=palette is not None,
        palette=palette,  # type: ignore[arg-type]
    )
    vec = vec if vec is not None else adata.obs[key]
    #
    return dict(zip(vec.cat.categories, [to_rgb(i) for i in adata.uns[Key.uns.colors(key)]]))


def _get_categorical(
    adata: AnnData,
    key: str,
    vec: pd.Series | None = None,
    palette: str | None = None,
    colordict: pd.Series | dict[Any, Any] | None = None,
) -> NDArrayA:
    categorical = vec if vec is not None else adata.obs[key]
    if not isinstance(colordict, dict):
        col_dict = _set_palette(adata, key, palette, colordict)
    else:
        col_dict = colordict
        for cat in colordict:
            if cat not in categorical.cat.categories:
                raise ValueError(
                    f"The key `{cat}` in the given dictionary is not an existing category in anndata[`{key}`]."
                )
            elif not is_color_like(colordict[cat]):  # noqa: RET506
                raise ValueError(f"`{colordict[cat]}` is not an acceptable color.")

    logger.debug(f"KEY: {key}")
    return np.array([col_dict[v] for v in categorical])


def _position_cluster_labels(coords: NDArrayA, clusters: pd.Series) -> dict[str, NDArrayA]:
    if not is_categorical_dtype(clusters):
        raise TypeError(f"Expected `clusters` to be `categorical`, found `{infer_dtype(clusters)}`.")
    coords = coords[:, 1:]
    df = pd.DataFrame(coords)
    df["clusters"] = clusters.values
    df = df.groupby("clusters")[[0, 1]].apply(lambda g: list(np.median(g.values, axis=0)))
    df = pd.DataFrame(list(df), index=df.index).dropna()
    kdtree = KDTree(coords)
    clusters = np.full(len(coords), fill_value="", dtype=object)
    # index consists of the categories that need not be string
    clusters[kdtree.query(df.values)[1]] = df.index.astype(str)
    return {"clusters": clusters}


def _min_max_norm(vec: spmatrix | NDArrayA) -> NDArrayA:
    if issparse(vec):
        if TYPE_CHECKING:
            assert isinstance(vec, spmatrix)
        vec = vec.toarray().squeeze()
    vec = np.asarray(vec, dtype=np.float64)
    if vec.ndim != 1:
        raise ValueError(f"Expected `1` dimension, found `{vec.ndim}`.")

    maxx, minn = np.nanmax(vec), np.nanmin(vec)

    return (  # type: ignore[no-any-return]
        np.ones_like(vec) if np.isclose(minn, maxx) else ((vec - minn) / (maxx - minn))
    )


def _swap_coordinates(data: list[Any]) -> list[Any]:
    return [[(y, x) for x, y in sublist] for sublist in data]


def _get_transform(element: SpatialElement, coordinate_system_name: str | None = None) -> None | NDArrayA:
    if not isinstance(element, (SpatialImage, MultiscaleSpatialImage, DaskDataFrame, GeoDataFrame)):
        raise RuntimeError("Cannot get transform for {type(element)}")

    transformations = get_transformation(element, get_all=True)
    cs = transformations.keys().__iter__().__next__() if coordinate_system_name is None else coordinate_system_name
    ct = transformations.get(cs)
    if ct:
        return ct.to_affine_matrix(input_axes=("y", "x"), output_axes=("y", "x"))  # type: ignore
    return None


@njit(cache=True, fastmath=True)
def _point_inside_triangles(triangles: NDArrayA) -> np.bool_:
    # modified from napari
    AB = triangles[:, 1, :] - triangles[:, 0, :]
    AC = triangles[:, 2, :] - triangles[:, 0, :]
    BC = triangles[:, 2, :] - triangles[:, 1, :]

    s_AB = -AB[:, 0] * triangles[:, 0, 1] + AB[:, 1] * triangles[:, 0, 0] >= 0
    s_AC = -AC[:, 0] * triangles[:, 0, 1] + AC[:, 1] * triangles[:, 0, 0] >= 0
    s_BC = -BC[:, 0] * triangles[:, 1, 1] + BC[:, 1] * triangles[:, 1, 0] >= 0

    return np.any((s_AB != s_AC) & (s_AB == s_BC))


@njit(parallel=True)
def _points_inside_triangles(points: NDArrayA, triangles: NDArrayA) -> NDArrayA:
    out = np.empty(
        len(
            points,
        ),
        dtype=np.bool_,
    )
    for i in prange(len(out)):
        out[i] = _point_inside_triangles(triangles - points[i])

    return out


def _adjust_channels_order(element: SpatialImage | MultiscaleSpatialImage) -> tuple[DataArray, bool]:
    """Swap the axes to y, x, c and check if an image supports rgb(a) visualization.

    Checks whether c dim is present in the axes and if so, transposes the dimensions to have c last.
    If the dimension of c is 3 or 4, it is assumed that the image is suitable for rgb(a) visualization.

    Parameters
    ----------
    element: SpatialImage | MultiScaleSpatialImage
        Element in sdata.images

    Returns
    -------
    new_raster: DataArray
        The image in shape of (c, y, x)
    rgb: bool
        Flag indicating suitability for rgb(a) visualization.
    """
    axes = get_axes_names(element)

    if "c" in axes:
        assert axes.index("c") == 0
        if isinstance(element, SpatialImage):
            n_channels = element.shape[0]
        elif isinstance(element, MultiscaleSpatialImage):
            v = element["scale0"].values()
            assert len(v) == 1
            n_channels = v.__iter__().__next__().shape[0]
        else:
            raise TypeError(f"Unsupported type for images or labels: {type(element)}")
    else:
        n_channels = 0

    if n_channels in [3, 4]:
        rgb = True
        new_raster = element.transpose("y", "x", "c")
    else:
        rgb = False
        new_raster = element

    # TODO: after we call .transpose() on a MultiscaleSpatialImage object we get a DataTree object. We should look at
    # this better and either cast somehow back to MultiscaleSpatialImage or fix/report this
    if isinstance(new_raster, (MultiscaleSpatialImage, DataTree)):
        list_of_xdata = []
        for k in new_raster:
            v = new_raster[k].values()
            assert len(v) == 1
            xdata = v.__iter__().__next__()
            list_of_xdata.append(xdata)
        new_raster = list_of_xdata

    return new_raster, rgb


def _get_sdata_key(sdata: EventedList, elements: dict[str, dict[str, str | int]], key: str) -> tuple[SpatialData, bool]:
    """
    Get the index of SpatialData object and key of SpatialElement.

    Parameters
    ----------
    sdata: EventedList
        EventedList containing the SpatialData objects currently associated with the viewer.
    elements: dict[str, dict[str, str | int]]
        Dictionary from elements widget containing the keyname as keys and a dictionary with the type of element, index
        of the SpatialData object and the original name in the SpatialData object.
    key: str
        The name of the item in the element widget.

    Returns
    -------
    tuple[SpatialData, bool]
        The SpatialData object which contains the element and a boolean indicating whether the element has duplicate
        name with other elements in other SpatialData objects.
    """
    sdata_index = elements[key]["sdata_index"]
    multi = False
    if key != elements[key]["original_name"]:
        multi = True

    return sdata[sdata_index], multi


def get_duplicate_element_names(sdata_ls: EventedList) -> tuple[list[str], list[str]]:
    """
    Get duplicate element names of a list of SpatialData objects.

    Parameters
    ----------
    sdata_ls: EventedList[SpatialData]
        Evented list of SpatialData objects

    Returns
    -------
    tuple[list[str], list[str]]
        The duplicate element names and the full list of element names
    """
    element_names = [element_name for sdata in sdata_ls for _, element_name, _ in sdata._gen_elements()]
    return [element for element, count in Counter(element_names).items() if count > 1], element_names


def get_elements_meta_mapping(
    sdatas: EventedList,
    coordinate_system_name: QListWidgetItem | Iterable[str],
    duplicate_element_names: list[str],
    key: None | str = None,
) -> tuple[dict[str, dict[str, str | int]], None | str]:
    """
    Get an element to metadata mapping and optionally retrieve the layer name to be added.

    Elements are mapped to their metadata. The element_names dictionary keys are adjusted if duplicate element names
    exist within the SpatialData objects. Optionally, the layer name to add can be retrieved for a particular element
    if added with Interactive.add_element.

    Parameters
    ----------
    sdatas: EventedList
        Napari EventedList containing the SpatialData objects
    coordinate_system_name: str
        The coordinate system to filter on.
    duplicate_element_names:
        A list of elements with duplicate names in the SpatialData objects in sdatas.
    key: None | str
        The element name of the element to be added as layer.

    Returns
    -------
    elements: dict[str, dict[str, str | int]]
        The element name to metadata mapping.
    name_to_add: None | str
        The name of the layer to add.
    """
    elements = {}
    name_to_add = None
    for index, sdata in enumerate(sdatas):
        for element_type, element_name, _ in sdata.filter_by_coordinate_system(coordinate_system_name)._gen_elements():
            elements_metadata = {
                "element_type": element_type,
                "sdata_index": index,
                "original_name": element_name,
            }
            name = element_name if element_name not in duplicate_element_names else element_name + f"_{index}"
            if key and element_name == key:
                name_to_add = name
            elements[name] = elements_metadata
    return elements, name_to_add
