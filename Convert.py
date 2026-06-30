from pathlib import Path
import json
import ssl
import shutil
import subprocess
import urllib.request

import laspy
import numpy as np
import pandas as pd

# ============================================================
# USER SETTINGS
# ============================================================

SOURCE_GEOID12B_FOLDER = Path(
    r"C:\Users\Durga\Downloads\geoid\2018\New_test\Geoid_12"
)

EXISTING_GEOID99_FOLDER = Path(
    r"C:\Users\Durga\Downloads\geoid\2018\New_test\Geoid_99"
)

OUTPUT_GENERATED_GEOID99_FOLDER = Path(
    r"C:\Users\Durga\Downloads\geoid\2018\New_test\Generated_Geoid99_FIXED_6588"
)

OUTPUT_COMPARE_CSV = Path(
    r"C:\Users\Durga\Downloads\geoid\2018\New_test\Generated_Geoid99_FIXED_6588\comparison_after_each_file.csv"
)



SOURCE_VERTICAL_UNITS_OVERRIDE = None
TARGET_VERTICAL_UNITS_OVERRIDE = None

# Process only first 2 files for testing
MAX_FILES_TO_PROCESS = 16

# Source CRS: your GEOID12B LAS files
SOURCE_HORIZONTAL_EPSG = "EPSG:26915"  # NAD83 / UTM zone 15N, meters

# Target CRS: should match original GEOID99 files
TARGET_HORIZONTAL_EPSG = "EPSG:6588"   # NAD83(2011) / Texas South Central (ftUS)

GRID_DIR = Path(
    r"C:\Users\Durga\Downloads\geoid\proj_grids"
)

PDAL_EXE = shutil.which("pdal") or r"C:\ProgramData\anaconda3\envs\lidar311\Library\bin\pdal.exe"

# ============================================================


GEOID12B = GRID_DIR / "us_noaa_g2012bu0.tif"

GEOID99_GRIDS = [
    GRID_DIR / f"us_noaa_g1999u0{i}.tif"
    for i in range(1, 9)
]

GRID_URLS = {
    GEOID12B: "https://cdn.proj.org/us_noaa_g2012bu0.tif",
}

for i in range(1, 9):
    grid = GRID_DIR / f"us_noaa_g1999u0{i}.tif"
    GRID_URLS[grid] = f"https://cdn.proj.org/us_noaa_g1999u0{i}.tif"




def get_las_crs_object(las_file: Path):
    """
    Reads CRS directly from LAS/LAZ header.
    """
    with laspy.open(las_file, read_evlrs=True) as reader:
        crs = reader.header.parse_crs()

    if crs is None:
        raise ValueError(f"No CRS found in LAS header: {las_file}")

    return crs


def get_horizontal_crs(crs):
    """
    If CRS is compound, use the horizontal/projected part.
    Otherwise return the CRS itself.
    """
    try:
        sub_crs_list = crs.sub_crs_list

        if sub_crs_list:
            for sub_crs in sub_crs_list:
                if getattr(sub_crs, "is_projected", False) or getattr(sub_crs, "is_geographic", False):
                    return sub_crs
    except Exception:
        pass

    return crs


def crs_to_pdal_horizontal_string(crs):
    """
    Convert CRS from LAS header into something PDAL/PROJ can use.
    Prefer EPSG if available. Otherwise use PROJ string. Otherwise WKT.
    """
    horizontal_crs = get_horizontal_crs(crs)

    try:
        epsg = horizontal_crs.to_epsg()
        if epsg is not None:
            return f"+init=epsg:{epsg}"
    except Exception:
        pass

    try:
        proj4 = horizontal_crs.to_proj4()
        if proj4:
            return proj4
    except Exception:
        pass

    return horizontal_crs.to_wkt()


def infer_vertical_units_from_crs(crs):
    """
    Infer LAS Z units from CRS text.
    For these datasets, LAS Z units usually follow the CRS linear units.
    """
    try:
        text = (crs.name + " " + crs.to_wkt()).lower()
    except Exception:
        text = str(crs).lower()

    if "us survey foot" in text or "ftus" in text or "foot_us" in text:
        return "us-ft"

    if "metre" in text or "meter" in text or "utm" in text:
        return "m"

    raise ValueError(
        "Could not infer vertical units from CRS. "
        "Set SOURCE_VERTICAL_UNITS_OVERRIDE manually to either 'm' or 'us-ft'."
    )


def get_point_count_simple(las_file: Path):
    with laspy.open(las_file, read_evlrs=True) as reader:
        return int(reader.header.point_count)



def list_las_files(folder: Path):
    files = []

    for p in folder.iterdir():
        if p.is_file() and p.suffix.lower() in [".las", ".laz"]:
            files.append(p)

    return sorted(files, key=lambda x: x.name.lower())


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


def download_grid_files():
    GRID_DIR.mkdir(parents=True, exist_ok=True)

    for grid_path, url in GRID_URLS.items():
        if grid_path.exists():
            print(f"Grid exists: {grid_path.name}")
            continue

        print(f"Downloading: {grid_path.name}")
        download_file(url, grid_path)
        print(f"Saved: {grid_path}")


def get_existing_geoid99_wkt(existing_file: Path):
    with laspy.open(existing_file, read_evlrs=True) as reader:
        crs = reader.header.parse_crs()
        if crs is None:
            raise ValueError(f"No CRS found in existing GEOID99 file: {existing_file}")

        return crs.to_wkt()


def convert_geoid12b_to_geoid99(source_file: Path, existing_file: Path, output_file: Path):
    output_file.parent.mkdir(parents=True, exist_ok=True)

    if output_file.exists():
        output_file.unlink()

    source_crs = get_las_crs_object(source_file)
    target_crs = get_las_crs_object(existing_file)

    source_horizontal_srs = crs_to_pdal_horizontal_string(source_crs)
    target_horizontal_srs = crs_to_pdal_horizontal_string(target_crs)

    source_vunits = SOURCE_VERTICAL_UNITS_OVERRIDE or infer_vertical_units_from_crs(source_crs)
    target_vunits = TARGET_VERTICAL_UNITS_OVERRIDE or infer_vertical_units_from_crs(target_crs)

    target_wkt = target_crs.to_wkt()

    geoid99_grid_string = ",".join(g.as_posix() for g in GEOID99_GRIDS)

    source_srs = (
        f"{source_horizontal_srs} "
        f"+vunits={source_vunits} "
        f"+geoidgrids={GEOID12B.as_posix()}"
    )

    target_srs = (
        f"{target_horizontal_srs} "
        f"+vunits={target_vunits} "
        f"+geoidgrids={geoid99_grid_string}"
    )

    print("Source CRS from header:")
    print(" ", source_crs.name)
    print("Target CRS from existing GEOID99:")
    print(" ", target_crs.name)
    print("Source SRS used by PDAL:")
    print(" ", source_srs)
    print("Target SRS used by PDAL:")
    print(" ", target_srs)

    pipeline = {
        "pipeline": [
            {
                "type": "readers.las",
                "filename": str(source_file)
            },
            {
                "type": "filters.reprojection",
                "in_srs": source_srs,
                "out_srs": target_srs
            },
            {
                "type": "writers.las",
                "filename": str(output_file),
                "a_srs": target_wkt,
                "scale_x": 0.01,
                "scale_y": 0.01,
                "scale_z": 0.01,
                "offset_x": "auto",
                "offset_y": "auto",
                "offset_z": "auto"
            }
        ]
    }

    result = subprocess.run(
        [PDAL_EXE, "pipeline", "--stdin"],
        input=json.dumps(pipeline),
        text=True,
        capture_output=True
    )

    print("PDAL return code:", result.returncode)

    if result.stdout:
        print("PDAL STDOUT:")
        print(result.stdout)

    if result.stderr:
        print("PDAL STDERR:")
        print(result.stderr)

    if result.returncode != 0:
        raise RuntimeError("PDAL pipeline failed.")

    generated_count = get_point_count_simple(output_file)

    print("Generated point count:", generated_count)

    if generated_count == 0:
        raise ValueError(
            f"Generated LAS has 0 points. Bad conversion output: {output_file}"
        )

def get_crs_name(las_file: Path):
    with laspy.open(las_file, read_evlrs=True) as reader:
        crs = reader.header.parse_crs()
        return crs.name if crs else None


def compare_generated_to_existing(generated_file: Path, existing_file: Path):
    with laspy.open(generated_file, read_evlrs=True) as gen_reader, laspy.open(existing_file, read_evlrs=True) as org_reader:
        gen_h = gen_reader.header
        org_h = org_reader.header

        gen_crs = gen_h.parse_crs()
        org_crs = org_h.parse_crs()

        row = {
            "generated_file": generated_file.name,
            "existing_file": existing_file.name,

            "generated_crs": gen_crs.name if gen_crs else None,
            "existing_crs": org_crs.name if org_crs else None,
            "crs_same": (gen_crs.name if gen_crs else None) == (org_crs.name if org_crs else None),

            "generated_point_count": int(gen_h.point_count),
            "existing_point_count": int(org_h.point_count),
            "point_count_diff": int(gen_h.point_count) - int(org_h.point_count),

            "generated_min_x": gen_h.mins[0],
            "existing_min_x": org_h.mins[0],
            "min_x_diff": gen_h.mins[0] - org_h.mins[0],

            "generated_min_y": gen_h.mins[1],
            "existing_min_y": org_h.mins[1],
            "min_y_diff": gen_h.mins[1] - org_h.mins[1],

            "generated_min_z": gen_h.mins[2],
            "existing_min_z": org_h.mins[2],
            "min_z_diff": gen_h.mins[2] - org_h.mins[2],

            "generated_max_z": gen_h.maxs[2],
            "existing_max_z": org_h.maxs[2],
            "max_z_diff": gen_h.maxs[2] - org_h.maxs[2],
        }

        if gen_h.point_count != org_h.point_count:
            row["point_by_point_status"] = "Skipped - point counts differ"
            return row

        dx_sum = dy_sum = dz_sum = 0.0
        dx_min = dy_min = dz_min = np.inf
        dx_max = dy_max = dz_max = -np.inf
        n_total = 0

        for gp, op in zip(
            gen_reader.chunk_iterator(1_000_000),
            org_reader.chunk_iterator(1_000_000)
        ):
            dx = np.asarray(gp.x) - np.asarray(op.x)
            dy = np.asarray(gp.y) - np.asarray(op.y)
            dz = np.asarray(gp.z) - np.asarray(op.z)

            dx_min = min(dx_min, dx.min())
            dx_max = max(dx_max, dx.max())
            dx_sum += dx.sum()

            dy_min = min(dy_min, dy.min())
            dy_max = max(dy_max, dy.max())
            dy_sum += dy.sum()

            dz_min = min(dz_min, dz.min())
            dz_max = max(dz_max, dz.max())
            dz_sum += dz.sum()

            n_total += len(dz)

        row.update({
            "dx_min": dx_min,
            "dx_max": dx_max,
            "dx_mean": dx_sum / n_total,

            "dy_min": dy_min,
            "dy_max": dy_max,
            "dy_mean": dy_sum / n_total,

            "dz_min": dz_min,
            "dz_max": dz_max,
            "dz_mean": dz_sum / n_total,
            "abs_dz_mean": abs(dz_sum / n_total),

            "point_by_point_status": "Done"
        })

        return row


def write_comparison_csv(rows):
    OUTPUT_COMPARE_CSV.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(OUTPUT_COMPARE_CSV, index=False)
    print(f"Updated CSV: {OUTPUT_COMPARE_CSV}")


def main():
    print("PDAL used:", PDAL_EXE)

    download_grid_files()

    source_files = list_las_files(SOURCE_GEOID12B_FOLDER)
    existing_files = list_las_files(EXISTING_GEOID99_FOLDER)

    print("\nSource files:")
    for f in source_files:
        print(" ", f.name)

    print("\nExisting GEOID99 files:")
    for f in existing_files:
        print(" ", f.name)

    print("\nSource count:", len(source_files))
    print("Existing count:", len(existing_files))

    if len(source_files) != len(existing_files):
        raise ValueError("Source and existing GEOID99 folders do not have equal LAS file counts.")

    source_files = source_files[:MAX_FILES_TO_PROCESS]
    existing_files = existing_files[:MAX_FILES_TO_PROCESS]

    print(f"\nProcessing first {len(source_files)} file pairs only.")

    comparison_rows = []

    for index, (source_file, existing_file) in enumerate(zip(source_files, existing_files), start=1):
        output_file = OUTPUT_GENERATED_GEOID99_FOLDER / f"{source_file.stem}_generated_geoid99_fixed_6588.las"

        print("\n" + "=" * 80)
        print(f"Pair {index}")
        print("Source GEOID12B:", source_file.name)
        print("Existing GEOID99:", existing_file.name)
        print("Output:", output_file.name)

        print("Converting...")
        convert_geoid12b_to_geoid99(source_file, existing_file, output_file)

        print("Output CRS check:")
        print(" ", get_crs_name(output_file))

        print("Comparing generated vs existing...")
        row = compare_generated_to_existing(output_file, existing_file)
        comparison_rows.append(row)

        write_comparison_csv(comparison_rows)

        print("Done with this file.")
        print("dz_mean:", row.get("dz_mean"))
        print("dx_mean:", row.get("dx_mean"))
        print("dy_mean:", row.get("dy_mean"))

    print("\nAll requested files completed.")
    print("Final CSV:", OUTPUT_COMPARE_CSV)


if __name__ == "__main__":
    main()
