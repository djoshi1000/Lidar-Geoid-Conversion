from pathlib import Path
import numpy as np
import pandas as pd
import rasterio
from rasterio.warp import reproject, transform_bounds, Resampling
from rasterio.coords import BoundingBox

# ============================================================
# USER SETTINGS
# ============================================================

SOURCE_DEM_FOLDER = Path(
    r"C:\Users\Durga\Downloads\geoid\2018\Geoid\HydroFlattened_DEM"
)

TARGET_DEM_FOLDER = Path(
    r"C:\Users\Durga\Downloads\geoid\2018\Geoid99\Hydroflattened_DEM"
)

OUTPUT_FOLDER = Path(
    r"C:\Users\Durga\Downloads\geoid\2018\DEM_Comparison_Output"
)

OUTPUT_CSV = OUTPUT_FOLDER / "hydroflattened_dem_overlap_comparison.csv"

# If source DEM values are meters and target DEM values are US survey feet:
SOURCE_Z_SCALE = 3.280833333333333

# If source DEM already has feet values, use:
# SOURCE_Z_SCALE = 1.0

# Optional vertical correction after scaling.
# Use 0 first. If trying to match your LAS-derived bias, test +0.248.
SOURCE_Z_OFFSET = 0.0

# Compare all overlaps above this threshold.
MIN_OVERLAP_PERCENT_OF_TARGET = 1.0

SAVE_ALIGNED_RASTERS = True

DST_NODATA = -999999.0

# ============================================================


def list_rasters(folder):
    files = []
    for p in folder.iterdir():
        if p.is_file() and p.suffix.lower() in [".tif",".img", ".tiff"]:
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


def find_overlapping_targets(source_path, target_files):
    overlaps = []

    with rasterio.open(source_path) as src:
        if src.crs is None:
            raise ValueError(f"Source raster has no CRS: {source_path}")

        for target_path in target_files:
            with rasterio.open(target_path) as target:
                if target.crs is None:
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


def valid_mask(arr, nodata):
    mask = np.isfinite(arr)

    if nodata is not None:
        mask &= arr != nodata

    mask &= arr != DST_NODATA
    return mask

def compare_source_to_target(source_path, target_path, overlap_pct_target, overlap_pct_source):
    with rasterio.open(source_path) as src, rasterio.open(target_path) as target:
        source = src.read(1).astype("float32")

        destination = np.full(
            shape=(target.height, target.width),
            fill_value=DST_NODATA,
            dtype="float32"
        )

        reproject(
            source=source,
            destination=destination,
            src_transform=src.transform,
            src_crs=src.crs,
            src_nodata=src.nodata,
            dst_transform=target.transform,
            dst_crs=target.crs,
            dst_nodata=DST_NODATA,
            resampling=Resampling.bilinear
        )

        generated = destination.astype("float64")
        generated[generated != DST_NODATA] = (
            generated[generated != DST_NODATA] * SOURCE_Z_SCALE + SOURCE_Z_OFFSET
        )

        target_arr = target.read(1).astype("float64")

        valid = valid_mask(generated, DST_NODATA) & valid_mask(target_arr, target.nodata)

        source_input_crs = str(src.crs)
        generated_output_crs = str(target.crs)
        target_existing_crs = str(target.crs)

        if valid.sum() == 0:
            return {
                "source_file": source_path.name,
                "target_file": target_path.name,
                "status": "No valid overlap after reprojection",

                "source_input_crs": source_input_crs,
                "generated_output_crs": generated_output_crs,
                "target_existing_crs": target_existing_crs,
                "generated_matches_target_crs": generated_output_crs == target_existing_crs,

                "overlap_pct_target": overlap_pct_target,
                "overlap_pct_source": overlap_pct_source,
            }

        diff = generated[valid] - target_arr[valid]
        abs_diff = np.abs(diff)

        row = {
            "source_file": source_path.name,
            "target_file": target_path.name,
            "status": "Compared",

            "source_input_crs": source_input_crs,
            "generated_output_crs": generated_output_crs,
            "target_existing_crs": target_existing_crs,

            "source_to_target_crs_same_before_conversion": source_input_crs == target_existing_crs,
            "generated_matches_target_crs": generated_output_crs == target_existing_crs,

            "source_width": src.width,
            "source_height": src.height,
            "target_width": target.width,
            "target_height": target.height,

            "source_res_x": src.res[0],
            "source_res_y": src.res[1],
            "target_res_x": target.res[0],
            "target_res_y": target.res[1],

            "source_z_scale_applied": SOURCE_Z_SCALE,
            "source_z_offset_applied": SOURCE_Z_OFFSET,

            "overlap_pct_target": overlap_pct_target,
            "overlap_pct_source": overlap_pct_source,

            "target_total_pixels": target_arr.size,
            "valid_overlap_pixels": int(valid.sum()),
            "valid_overlap_pct_of_target": float(valid.sum() / target_arr.size * 100),

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
        }

        if SAVE_ALIGNED_RASTERS:
            aligned_folder = OUTPUT_FOLDER / "aligned_source_to_target_grid"
            aligned_folder.mkdir(parents=True, exist_ok=True)

            aligned_path = aligned_folder / f"{source_path.stem}_to_{target_path.stem}_aligned.tif"

            profile = target.profile.copy()
            profile.update({
                "driver": "GTiff",
                "dtype": "float32",
                "count": 1,
                "nodata": DST_NODATA,
                "compress": "lzw"
            })

            out_arr = generated.astype("float32")
            out_arr[~np.isfinite(out_arr)] = DST_NODATA

            with rasterio.open(aligned_path, "w", **profile) as dst:
                dst.write(out_arr, 1)

            row["aligned_raster"] = str(aligned_path)

        return row

def write_csv(rows):
    OUTPUT_FOLDER.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(OUTPUT_CSV, index=False)
    print("Updated CSV:", OUTPUT_CSV)


def main():
    OUTPUT_FOLDER.mkdir(parents=True, exist_ok=True)

    source_files = list_rasters(SOURCE_DEM_FOLDER)
    target_files = list_rasters(TARGET_DEM_FOLDER)

    print("Source DEM files:", len(source_files))
    print("Target GEOID99 DEM files:", len(target_files))

    rows = []

    for source_path in source_files:
        print("\n" + "=" * 80)
        print("Source:", source_path.name)

        overlaps = find_overlapping_targets(source_path, target_files)

        if not overlaps:
            print("No overlapping target found.")
            rows.append({
                "source_file": source_path.name,
                "target_file": None,
                "status": "No overlapping target found"
            })
            write_csv(rows)
            continue

        print("Overlapping targets:")
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

            print("Comparing with:", target_path.name)

            row = compare_source_to_target(
                source_path,
                target_path,
                item["overlap_pct_target"],
                item["overlap_pct_source"]
            )

            rows.append(row)
            write_csv(rows)

            print("Status:", row["status"])
            if row["status"] == "Compared":
                print("diff_mean:", row["diff_mean"])
                print("diff_median:", row["diff_median"])
                print("rmse:", row["rmse"])
                print("mae:", row["mae"])

    print("\nDone.")
    print("Final CSV:", OUTPUT_CSV)


if __name__ == "__main__":
    main()
