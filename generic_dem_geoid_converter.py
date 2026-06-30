from pathlib import Path
import ssl
import shutil
import urllib.request

import numpy as np
import pandas as pd
import rasterio
from rasterio.warp import reproject, transform_bounds, Resampling
from rasterio.coords import BoundingBox

try:
    from pyproj import CRS, Transformer
except Exception as exc:
    raise ImportError(
        "This script needs pyproj for true vertical geoid conversion. "
        "Install it in your environment using: conda install -c conda-forge pyproj"
    ) from exc


# ============================================================
# USER SETTINGS - CHANGE ONLY THIS SECTION
# ============================================================

SOURCE_DEM_FOLDER = Path(
    r"C:\Users\Durga\Downloads\geoid\2018\Geoid99\Hydroflattened_DEM"
)

TARGET_REFERENCE_DEM_FOLDER = Path(
    r"C:\Users\Durga\Downloads\geoid\2018\Geoid\HydroFlattened_DEM"
)

OUTPUT_FOLDER = Path(
    r"C:\Users\Durga\Downloads\geoid\2018\Generated_DEM_Geoid12B_from_Geoid99"
)

OUTPUT_CSV = OUTPUT_FOLDER / "dem_geoid_conversion_QA.csv"

# Examples:
#   GEOID99  -> GEOID12B: SOURCE_GEOID = "GEOID99",  TARGET_GEOID = "GEOID12B"
#   GEOID12B -> GEOID99 : SOURCE_GEOID = "GEOID12B", TARGET_GEOID = "GEOID99"
#   GEOID18  -> GEOID12B: SOURCE_GEOID = "GEOID18",  TARGET_GEOID = "GEOID12B"
SOURCE_GEOID = "GEOID99"
TARGET_GEOID = "GEOID12B"

# Use None to process all source rasters.
MAX_SOURCE_FILES = None

# Compare every overlapping target/reference raster above this overlap threshold.
MIN_OVERLAP_PERCENT_OF_TARGET = 1.0

# If True, compare only the best-overlap target/reference raster for each source raster.
# If False, compare all overlapping target/reference rasters.
COMPARE_ONLY_BEST_OVERLAP = False

# True = real geoid-to-geoid Z conversion using pyproj/PROJ geoid grids.
# False = old behavior: horizontal reprojection + source-to-target Z unit scaling only.
APPLY_VERTICAL_GEOID_TRANSFORM = True

# Use "AUTO" to infer from raster CRS horizontal units.
# Valid manual values: "m", "us-ft", "ft"
SOURCE_Z_UNITS = "AUTO"
TARGET_Z_UNITS = "AUTO"

# Optional final vertical offset in target units.
# Keep 0.0 first. Only use after QA proves a systematic production offset exists.
TARGET_Z_OFFSET = 0.0

# Output converted/aligned rasters for visual inspection.
SAVE_CONVERTED_RASTERS = True

# Output file type. GeoTIFF is safest for generated outputs.
OUTPUT_RASTER_EXTENSION = ".tif"

# Raster resampling for DEM elevation values.
DEM_RESAMPLING = Resampling.bilinear

# Processing block height for vertical transform. Lower if memory is limited.
BLOCK_ROWS = 512

# NoData value for generated output.
DST_NODATA = -999999.0

GRID_DIR = Path(
    r"C:\Users\Durga\Downloads\geoid\proj_grids"
)

# ============================================================
# GEOID GRID REGISTRY
# Add another geoid model here if needed.
# ============================================================

def make_geoid_registry(grid_dir: Path):
    return {
        "GEOID99": {
            "paths": [grid_dir / f"us_noaa_g1999u0{i}.tif" for i in range(1, 9)],
            "urls": [f"https://cdn.proj.org/us_noaa_g1999u0{i}.tif" for i in range(1, 9)],
            "aliases": ["G99", "GEOID1999", "99"],
        },
        "GEOID12B": {
            "paths": [grid_dir / "us_noaa_g2012bu0.tif"],
            "urls": ["https://cdn.proj.org/us_noaa_g2012bu0.tif"],
            "aliases": ["G12B", "GEOID2012B", "12B"],
        },
        "GEOID18": {
            "paths": [grid_dir / "us_noaa_g2018u0.tif"],
            "urls": ["https://cdn.proj.org/us_noaa_g2018u0.tif"],
            "aliases": ["G18", "GEOID2018", "18"],
        },
    }


GEOID_REGISTRY = make_geoid_registry(GRID_DIR)


# ============================================================
# GENERAL HELPERS
# ============================================================

def normalize_geoid_name(name: str) -> str:
    raw = str(name).strip().upper().replace("-", "").replace("_", "").replace(" ", "")

    for canonical_name, info in GEOID_REGISTRY.items():
        choices = [canonical_name] + info.get("aliases", [])
        choices = [
            c.upper().replace("-", "").replace("_", "").replace(" ", "")
            for c in choices
        ]

        if raw in choices:
            return canonical_name

    raise ValueError(
        f"Unsupported geoid model: {name}. "
        f"Available options are: {sorted(GEOID_REGISTRY.keys())}."
    )


def get_geoid_paths_and_urls(geoid_name: str):
    geoid_name = normalize_geoid_name(geoid_name)
    info = GEOID_REGISTRY[geoid_name]
    return info["paths"], info["urls"]


def get_geoid_grid_string(geoid_name: str) -> str:
    paths, _ = get_geoid_paths_and_urls(geoid_name)
    return ",".join(p.as_posix() for p in paths)


def make_ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def download_file(url, output_path):
    context = make_ssl_context()

    with urllib.request.urlopen(url, context=context) as response:
        with open(output_path, "wb") as f:
            shutil.copyfileobj(response, f)


def download_required_grid_files():
    GRID_DIR.mkdir(parents=True, exist_ok=True)

    needed = []
    for geoid_name in [SOURCE_GEOID, TARGET_GEOID]:
        paths, urls = get_geoid_paths_and_urls(geoid_name)
        needed.extend(zip(paths, urls))

    seen = set()
    unique_needed = []
    for path, url in needed:
        key = str(path).lower()
        if key not in seen:
            unique_needed.append((path, url))
            seen.add(key)

    for grid_path, url in unique_needed:
        if grid_path.exists():
            print(f"Grid exists: {grid_path.name}")
            continue

        print(f"Downloading: {grid_path.name}")
        download_file(url, grid_path)
        print(f"Saved: {grid_path}")


def list_rasters(folder: Path):
    if not folder.exists():
        raise FileNotFoundError(f"Folder does not exist: {folder}")

    files = []
    for p in folder.iterdir():
        if p.is_file() and p.suffix.lower() in [".tif", ".tiff", ".img"]:
            files.append(p)

    return sorted(files, key=lambda x: x.name.lower())


def bounds_area(b):
    return abs((b.right - b.left) * (b.top - b.bottom))


def intersection_bounds(b1, b2):
    left = max(b1.left, b2.left)
    right = min(b1.right, b2.right)
    bottom = max(b1.bottom, b2.bottom)
    top = min(b1.top, b2.top)

    if right <= left or top <= bottom:
        return None

    return BoundingBox(left, bottom, right, top)


def intersection_area(b1, b2):
    inter = intersection_bounds(b1, b2)
    if inter is None:
        return 0.0
    return bounds_area(inter)


def valid_mask(arr, nodata):
    mask = np.isfinite(arr)

    if nodata is not None:
        mask &= arr != nodata

    mask &= arr != DST_NODATA
    return mask


def raster_crs_to_pyproj(crs):
    if crs is None:
        return None
    return CRS.from_user_input(crs)


def get_horizontal_crs(crs):
    crs = CRS.from_user_input(crs)

    try:
        sub_crs_list = crs.sub_crs_list
        if sub_crs_list:
            for sub_crs in sub_crs_list:
                if sub_crs.is_projected or sub_crs.is_geographic:
                    return sub_crs
    except Exception:
        pass

    return crs


def crs_to_pdal_proj_string(crs):
    """
    Return a stable horizontal CRS string usable by PROJ/pyproj.
    Prefer EPSG when available.
    """
    horizontal = get_horizontal_crs(crs)

    try:
        epsg = horizontal.to_epsg()
        if epsg is not None:
            return f"+init=epsg:{epsg}"
    except Exception:
        pass

    try:
        proj4 = horizontal.to_proj4()
        if proj4:
            return proj4
    except Exception:
        pass

    return horizontal.to_wkt()


def infer_z_units_from_crs(crs):
    """
    DEM vertical units are not always encoded in raster CRS.
    This function infers from the horizontal CRS unit, which matches your current datasets:
    UTM source DEM = meters, StatePlane GEOID99 DEM = US survey feet.
    Override SOURCE_Z_UNITS/TARGET_Z_UNITS when needed.
    """
    pycrs = CRS.from_user_input(crs)

    try:
        axis_units = " ".join(
            [
                (axis.unit_name or "")
                for axis in pycrs.axis_info
            ]
        ).lower()
    except Exception:
        axis_units = ""

    text = (str(pycrs.name) + " " + axis_units + " " + pycrs.to_wkt()).lower()

    if "us survey foot" in text or "foot_us" in text or "ftus" in text:
        return "us-ft"

    if "foot" in text or "feet" in text:
        return "ft"

    if "metre" in text or "meter" in text or "utm" in text:
        return "m"

    raise ValueError(
        "Could not infer DEM Z units from CRS. "
        "Set SOURCE_Z_UNITS and TARGET_Z_UNITS manually to 'm', 'us-ft', or 'ft'."
    )


def resolve_z_units(crs, setting_value):
    value = str(setting_value).strip().lower()

    if value == "auto":
        return infer_z_units_from_crs(crs)

    if value in ["m", "meter", "meters", "metre", "metres"]:
        return "m"

    if value in ["us-ft", "us_survey_ft", "us survey foot", "ftus", "foot_us"]:
        return "us-ft"

    if value in ["ft", "foot", "feet", "international foot"]:
        return "ft"

    raise ValueError(
        f"Invalid Z unit setting: {setting_value}. "
        "Use 'AUTO', 'm', 'us-ft', or 'ft'."
    )


def z_unit_to_meter_factor(unit_name: str):
    unit_name = resolve_unit_name(unit_name)

    if unit_name == "m":
        return 1.0

    if unit_name == "us-ft":
        return 1200.0 / 3937.0

    if unit_name == "ft":
        return 0.3048

    raise ValueError(f"Unsupported unit: {unit_name}")


def resolve_unit_name(unit_name: str):
    value = str(unit_name).strip().lower()

    if value in ["m", "meter", "meters", "metre", "metres"]:
        return "m"

    if value in ["us-ft", "us_survey_ft", "us survey foot", "ftus", "foot_us"]:
        return "us-ft"

    if value in ["ft", "foot", "feet", "international foot"]:
        return "ft"

    raise ValueError(f"Unsupported unit: {unit_name}")


def z_scale_factor(source_units: str, target_units: str):
    source_m = z_unit_to_meter_factor(source_units)
    target_m = z_unit_to_meter_factor(target_units)
    return source_m / target_m


def build_geoid_srs(crs, geoid_name: str, z_units: str):
    horizontal_srs = crs_to_pdal_proj_string(crs)
    geoid_grid_string = get_geoid_grid_string(geoid_name)

    return (
        f"{horizontal_srs} "
        f"+vunits={z_units} "
        f"+geoidgrids={geoid_grid_string}"
    )


def build_horizontal_transformer(from_crs, to_crs):
    from_horizontal = get_horizontal_crs(from_crs)
    to_horizontal = get_horizontal_crs(to_crs)
    return Transformer.from_crs(from_horizontal, to_horizontal, always_xy=True)


def grid_xy_from_transform(transform, width, row_start, row_stop):
    """
    Return target raster cell-center X/Y arrays for rows [row_start, row_stop).
    Works for north-up and rotated affine transforms.
    """
    rows = np.arange(row_start, row_stop, dtype="float64") + 0.5
    cols = np.arange(0, width, dtype="float64") + 0.5

    col_grid, row_grid = np.meshgrid(cols, rows)

    x = transform.c + transform.a * col_grid + transform.b * row_grid
    y = transform.f + transform.d * col_grid + transform.e * row_grid

    return x, y


# ============================================================
# OVERLAP MATCHING
# ============================================================

def find_overlapping_targets(source_path, target_files):
    overlaps = []

    with rasterio.open(source_path) as src:
        if src.crs is None:
            raise ValueError(f"Source raster has no CRS: {source_path}")

        for target_path in target_files:
            with rasterio.open(target_path) as target:
                if target.crs is None:
                    print(f"Skipping target with no CRS: {target_path.name}")
                    continue

                src_bounds_in_target = BoundingBox(
                    *transform_bounds(
                        src.crs,
                        target.crs,
                        *src.bounds,
                        densify_pts=21
                    )
                )

                area = intersection_area(src_bounds_in_target, target.bounds)
                target_area = bounds_area(target.bounds)
                source_area = bounds_area(src_bounds_in_target)

                overlap_pct_target = area / target_area * 100 if target_area else 0
                overlap_pct_source = area / source_area * 100 if source_area else 0

                if overlap_pct_target >= MIN_OVERLAP_PERCENT_OF_TARGET:
                    overlaps.append({
                        "source_path": source_path,
                        "target_path": target_path,
                        "source_bounds_in_target": src_bounds_in_target,
                        "overlap_area": area,
                        "overlap_pct_target": overlap_pct_target,
                        "overlap_pct_source": overlap_pct_source,
                    })

    return sorted(overlaps, key=lambda x: -x["overlap_area"])


# ============================================================
# CONVERSION
# ============================================================

def horizontally_align_source_to_target_grid(src, target):
    """
    Reproject/resample source raster values onto the target/reference raster grid.
    The output values are still in the source vertical geoid and source Z unit.
    """
    source = src.read(1).astype("float32")

    aligned = np.full(
        shape=(target.height, target.width),
        fill_value=DST_NODATA,
        dtype="float32"
    )

    reproject(
        source=source,
        destination=aligned,
        src_transform=src.transform,
        src_crs=src.crs,
        src_nodata=src.nodata,
        dst_transform=target.transform,
        dst_crs=target.crs,
        dst_nodata=DST_NODATA,
        resampling=DEM_RESAMPLING
    )

    return aligned.astype("float64")


def apply_geoid_vertical_transform(
    aligned_source_on_target_grid,
    source_crs,
    target_crs,
    target_transform,
    source_geoid,
    target_geoid,
    source_z_units,
    target_z_units,
):
    """
    Convert DEM Z values from source geoid/unit to target geoid/unit.

    Important logic:
    1. Source DEM is first horizontally aligned to the target grid.
    2. For each target grid cell center, we transform the target XY back to source horizontal CRS.
    3. We then run a full 3D PROJ transform:
       source horizontal CRS + source geoid + source Z
       -> target horizontal CRS + target geoid + target Z.
    """
    source_srs = build_geoid_srs(source_crs, source_geoid, source_z_units)
    target_srs = build_geoid_srs(target_crs, target_geoid, target_z_units)

    target_to_source_xy = build_horizontal_transformer(target_crs, source_crs)
    geoid_transformer = Transformer.from_crs(source_srs, target_srs, always_xy=True)

    height, width = aligned_source_on_target_grid.shape
    converted = np.full_like(aligned_source_on_target_grid, DST_NODATA, dtype="float64")

    print("Source geoid SRS:")
    print(" ", source_srs)
    print("Target geoid SRS:")
    print(" ", target_srs)

    for row_start in range(0, height, BLOCK_ROWS):
        row_stop = min(row_start + BLOCK_ROWS, height)

        block = aligned_source_on_target_grid[row_start:row_stop, :]
        valid = valid_mask(block, DST_NODATA)

        if not np.any(valid):
            continue

        x_target, y_target = grid_xy_from_transform(
            target_transform,
            width,
            row_start,
            row_stop
        )

        x_source, y_source = target_to_source_xy.transform(
            x_target[valid],
            y_target[valid]
        )

        z_source = block[valid]

        _, _, z_target = geoid_transformer.transform(
            x_source,
            y_source,
            z_source
        )

        out_block = converted[row_start:row_stop, :]
        out_block[valid] = z_target

    return converted


def convert_source_to_target_reference_grid(source_path, target_path):
    source_geoid = normalize_geoid_name(SOURCE_GEOID)
    target_geoid = normalize_geoid_name(TARGET_GEOID)

    with rasterio.open(source_path) as src, rasterio.open(target_path) as target:
        if src.crs is None:
            raise ValueError(f"Source raster has no CRS: {source_path}")
        if target.crs is None:
            raise ValueError(f"Target/reference raster has no CRS: {target_path}")

        source_z_units = resolve_z_units(src.crs, SOURCE_Z_UNITS)
        target_z_units = resolve_z_units(target.crs, TARGET_Z_UNITS)

        aligned_source = horizontally_align_source_to_target_grid(src, target)

        if APPLY_VERTICAL_GEOID_TRANSFORM:
            generated = apply_geoid_vertical_transform(
                aligned_source_on_target_grid=aligned_source,
                source_crs=src.crs,
                target_crs=target.crs,
                target_transform=target.transform,
                source_geoid=source_geoid,
                target_geoid=target_geoid,
                source_z_units=source_z_units,
                target_z_units=target_z_units,
            )
            z_scale_applied = None
            conversion_method = "horizontal_reprojection_plus_pyproj_geoid_vertical_transform"
        else:
            scale = z_scale_factor(source_z_units, target_z_units)
            generated = aligned_source.copy()
            valid = valid_mask(generated, DST_NODATA)
            generated[valid] = generated[valid] * scale
            z_scale_applied = scale
            conversion_method = "horizontal_reprojection_plus_z_unit_scaling_only_no_geoid_shift"

        valid = valid_mask(generated, DST_NODATA)
        generated[valid] = generated[valid] + TARGET_Z_OFFSET

        profile = target.profile.copy()
        profile.update({
            "driver": "GTiff",
            "dtype": "float32",
            "count": 1,
            "nodata": DST_NODATA,
            "compress": "lzw",
            "crs": target.crs,
            "transform": target.transform,
            "width": target.width,
            "height": target.height,
        })

        metadata = {
            "source_input_crs": str(src.crs),
            "generated_output_crs": str(target.crs),
            "target_existing_crs": str(target.crs),
            "source_geoid": source_geoid,
            "target_geoid": target_geoid,
            "source_z_units": source_z_units,
            "target_z_units": target_z_units,
            "source_z_scale_applied_when_no_geoid_transform": z_scale_applied,
            "target_z_offset_applied": TARGET_Z_OFFSET,
            "conversion_method": conversion_method,
            "source_width": src.width,
            "source_height": src.height,
            "target_width": target.width,
            "target_height": target.height,
            "source_res_x": src.res[0],
            "source_res_y": src.res[1],
            "target_res_x": target.res[0],
            "target_res_y": target.res[1],
        }

        return generated, profile, metadata


def save_generated_raster(generated, profile, output_path):
    output_path.parent.mkdir(parents=True, exist_ok=True)

    out_arr = generated.astype("float32")
    out_arr[~np.isfinite(out_arr)] = DST_NODATA

    with rasterio.open(output_path, "w", **profile) as dst:
        dst.write(out_arr, 1)


# ============================================================
# QA COMPARISON
# ============================================================

def compare_generated_to_target(
    generated,
    target_path,
    source_path,
    output_raster_path,
    metadata,
    overlap_pct_target,
    overlap_pct_source,
):
    with rasterio.open(target_path) as target:
        target_arr = target.read(1).astype("float64")

        valid = valid_mask(generated, DST_NODATA) & valid_mask(target_arr, target.nodata)

        base_row = {
            "source_file": source_path.name,
            "target_reference_file": target_path.name,
            "generated_raster": str(output_raster_path) if output_raster_path else None,
            "status": "Compared" if valid.sum() > 0 else "No valid overlap after reprojection",

            "source_input_crs": metadata["source_input_crs"],
            "generated_output_crs": metadata["generated_output_crs"],
            "target_existing_crs": metadata["target_existing_crs"],
            "generated_matches_target_crs": metadata["generated_output_crs"] == metadata["target_existing_crs"],

            "source_geoid": metadata["source_geoid"],
            "target_geoid": metadata["target_geoid"],
            "source_z_units": metadata["source_z_units"],
            "target_z_units": metadata["target_z_units"],
            "conversion_method": metadata["conversion_method"],
            "source_z_scale_applied_when_no_geoid_transform": metadata["source_z_scale_applied_when_no_geoid_transform"],
            "target_z_offset_applied": metadata["target_z_offset_applied"],

            "source_width": metadata["source_width"],
            "source_height": metadata["source_height"],
            "target_width": metadata["target_width"],
            "target_height": metadata["target_height"],
            "source_res_x": metadata["source_res_x"],
            "source_res_y": metadata["source_res_y"],
            "target_res_x": metadata["target_res_x"],
            "target_res_y": metadata["target_res_y"],

            "overlap_pct_target": overlap_pct_target,
            "overlap_pct_source": overlap_pct_source,
            "target_total_pixels": int(target_arr.size),
            "valid_overlap_pixels": int(valid.sum()),
            "valid_overlap_pct_of_target": float(valid.sum() / target_arr.size * 100),
        }

        if valid.sum() == 0:
            return base_row

        diff = generated[valid] - target_arr[valid]
        abs_diff = np.abs(diff)

        base_row.update({
            "generated_min": float(generated[valid].min()),
            "generated_max": float(generated[valid].max()),
            "target_min": float(target_arr[valid].min()),
            "target_max": float(target_arr[valid].max()),

            "diff_min": float(diff.min()),
            "diff_max": float(diff.max()),
            "diff_mean": float(diff.mean()),
            "diff_median": float(np.median(diff)),
            "diff_std": float(diff.std()),

            "mae": float(abs_diff.mean()),
            "rmse": float(np.sqrt(np.mean(diff ** 2))),
            "abs_diff_median": float(np.median(abs_diff)),
            "abs_diff_95pct": float(np.percentile(abs_diff, 95)),
            "abs_diff_99pct": float(np.percentile(abs_diff, 99)),
            "abs_diff_max": float(abs_diff.max()),
        })

        return base_row


def write_csv(rows):
    OUTPUT_FOLDER.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(OUTPUT_CSV, index=False)
    print("Updated CSV:", OUTPUT_CSV)


# ============================================================
# MAIN
# ============================================================

def main():
    OUTPUT_FOLDER.mkdir(parents=True, exist_ok=True)

    source_geoid = normalize_geoid_name(SOURCE_GEOID)
    target_geoid = normalize_geoid_name(TARGET_GEOID)

    print("Source DEM folder:", SOURCE_DEM_FOLDER)
    print("Target/reference DEM folder:", TARGET_REFERENCE_DEM_FOLDER)
    print("Output folder:", OUTPUT_FOLDER)
    print("Conversion:", source_geoid, "->", target_geoid)
    print("Apply vertical geoid transform:", APPLY_VERTICAL_GEOID_TRANSFORM)

    download_required_grid_files()

    source_files = list_rasters(SOURCE_DEM_FOLDER)
    target_files = list_rasters(TARGET_REFERENCE_DEM_FOLDER)

    if MAX_SOURCE_FILES is not None:
        source_files = source_files[:MAX_SOURCE_FILES]

    print("Source DEM files:", len(source_files))
    print("Target/reference DEM files:", len(target_files))

    rows = []

    for source_index, source_path in enumerate(source_files, start=1):
        print("\n" + "=" * 80)
        print(f"Source {source_index}/{len(source_files)}:", source_path.name)

        overlaps = find_overlapping_targets(source_path, target_files)

        if not overlaps:
            print("No overlapping target/reference raster found.")
            rows.append({
                "source_file": source_path.name,
                "target_reference_file": None,
                "status": "No overlapping target/reference raster found",
                "source_geoid": source_geoid,
                "target_geoid": target_geoid,
            })
            write_csv(rows)
            continue

        if COMPARE_ONLY_BEST_OVERLAP:
            overlaps = overlaps[:1]

        print("Overlapping target/reference rasters:")
        for item in overlaps:
            print(
                " ",
                item["target_path"].name,
                "| target overlap %:",
                round(item["overlap_pct_target"], 2),
                "| source overlap %:",
                round(item["overlap_pct_source"], 2)
            )

        for item in overlaps:
            target_path = item["target_path"]

            print("Converting/comparing with:", target_path.name)

            generated, profile, metadata = convert_source_to_target_reference_grid(
                source_path,
                target_path
            )

            output_raster_path = None

            if SAVE_CONVERTED_RASTERS:
                output_folder = OUTPUT_FOLDER / "converted_source_to_target_grid"
                output_name = (
                    f"{source_path.stem}_to_{target_path.stem}_"
                    f"generated_{target_geoid.lower()}{OUTPUT_RASTER_EXTENSION}"
                )
                output_raster_path = output_folder / output_name

                save_generated_raster(
                    generated=generated,
                    profile=profile,
                    output_path=output_raster_path
                )

                print("Saved generated raster:", output_raster_path)

            row = compare_generated_to_target(
                generated=generated,
                target_path=target_path,
                source_path=source_path,
                output_raster_path=output_raster_path,
                metadata=metadata,
                overlap_pct_target=item["overlap_pct_target"],
                overlap_pct_source=item["overlap_pct_source"],
            )

            rows.append(row)
            write_csv(rows)

            print("Status:", row["status"])
            print("Generated matches target CRS:", row.get("generated_matches_target_crs"))
            print("Valid overlap pixels:", row.get("valid_overlap_pixels"))

            if row["status"] == "Compared":
                print("diff_mean:", row["diff_mean"])
                print("diff_median:", row["diff_median"])
                print("rmse:", row["rmse"])
                print("mae:", row["mae"])

    print("\nDone.")
    print("Final CSV:", OUTPUT_CSV)


if __name__ == "__main__":
    main()
