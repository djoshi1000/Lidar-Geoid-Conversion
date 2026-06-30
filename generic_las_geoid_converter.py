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
# USER SETTINGS - CHANGE ONLY THIS SECTION
# ============================================================

SOURCE_FOLDER = Path(
    r"C:\Users\Durga\Downloads\geoid\2018\New_test\Geoid_99"
)

TARGET_REFERENCE_FOLDER = Path(
    r"C:\Users\Durga\Downloads\geoid\2018\New_test\Geoid_12"
)

OUTPUT_FOLDER = Path(
    r"C:\Users\Durga\Downloads\geoid\2018\New_test\Generated_Geoid12B_from_Geoid99"
)

OUTPUT_COMPARE_CSV = OUTPUT_FOLDER / "conversion_QA.csv"

# Examples:
#   GEOID99  -> GEOID12B: SOURCE_GEOID = "GEOID99",  TARGET_GEOID = "GEOID12B"
#   GEOID12B -> GEOID99 : SOURCE_GEOID = "GEOID12B", TARGET_GEOID = "GEOID99"
#   GEOID18  -> GEOID12B: SOURCE_GEOID = "GEOID18",  TARGET_GEOID = "GEOID12B"
SOURCE_GEOID = "GEOID99"
TARGET_GEOID = "GEOID12B"

# Use None to process all files
MAX_FILES_TO_PROCESS = 16

# Output file extension
OUTPUT_EXTENSION = ".las"

# Optional vertical unit override.
# Usually keep these as None and let the script infer from CRS.
# Valid values: None, "m", "us-ft"
SOURCE_VERTICAL_UNITS_OVERRIDE = None
TARGET_VERTICAL_UNITS_OVERRIDE = None

# LAS writer scale. 0.01 is usually okay for ft or m outputs.
LAS_SCALE_X = 0.01
LAS_SCALE_Y = 0.01
LAS_SCALE_Z = 0.01

GRID_DIR = Path(
    r"C:\Users\Durga\Downloads\geoid\proj_grids"
)

PDAL_EXE = shutil.which("pdal") or r"C:\ProgramData\anaconda3\envs\lidar311\Library\bin\pdal.exe"

# ============================================================
# GEOID GRID REGISTRY
# Add new geoid models here if needed.
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
        choices = [c.upper().replace("-", "").replace("_", "").replace(" ", "") for c in choices]

        if raw in choices:
            return canonical_name

    available = sorted(GEOID_REGISTRY.keys())
    raise ValueError(
        f"Unsupported geoid model: {name}. Available options are: {available}. "
        f"To support another geoid, add it to GEOID_REGISTRY."
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

    # Remove duplicates while preserving order
    seen = set()
    unique_needed = []
    for path, url in needed:
        key = str(path).lower()
        if key not in seen:
            seen.add(key)
            unique_needed.append((path, url))

    for grid_path, url in unique_needed:
        if grid_path.exists():
            print(f"Grid exists: {grid_path.name}")
            continue

        print(f"Downloading: {grid_path.name}")
        download_file(url, grid_path)
        print(f"Saved: {grid_path}")


def list_las_files(folder: Path):
    if not folder.exists():
        raise FileNotFoundError(f"Folder does not exist: {folder}")

    files = []
    for p in folder.iterdir():
        if p.is_file() and p.suffix.lower() in [".las", ".laz"]:
            files.append(p)

    return sorted(files, key=lambda x: x.name.lower())


def make_reference_lookup(reference_files):
    return {p.name.lower(): p for p in reference_files}


def get_las_crs_object(las_file: Path):
    with laspy.open(las_file, read_evlrs=True) as reader:
        crs = reader.header.parse_crs()

    if crs is None:
        raise ValueError(f"No CRS found in LAS header: {las_file}")

    return crs


def get_horizontal_crs(crs):
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
        "Set SOURCE_VERTICAL_UNITS_OVERRIDE or TARGET_VERTICAL_UNITS_OVERRIDE manually to 'm' or 'us-ft'."
    )


def get_point_count_simple(las_file: Path):
    with laspy.open(las_file, read_evlrs=True) as reader:
        return int(reader.header.point_count)


def get_crs_name(las_file: Path):
    with laspy.open(las_file, read_evlrs=True) as reader:
        crs = reader.header.parse_crs()
        return crs.name if crs else None


def inspect_las_basic(las_file: Path):
    with laspy.open(las_file, read_evlrs=True) as reader:
        h = reader.header
        crs = h.parse_crs()

        return {
            "file": las_file.name,
            "point_count": int(h.point_count),
            "crs_name": crs.name if crs else None,
            "min_x": float(h.mins[0]),
            "min_y": float(h.mins[1]),
            "min_z": float(h.mins[2]),
            "max_x": float(h.maxs[0]),
            "max_y": float(h.maxs[1]),
            "max_z": float(h.maxs[2]),
        }


# ============================================================
# GENERIC CONVERSION FUNCTION
# ============================================================

def build_srs(crs, geoid_name: str, vertical_units_override=None):
    horizontal_srs = crs_to_pdal_horizontal_string(crs)
    vunits = vertical_units_override or infer_vertical_units_from_crs(crs)
    geoid_grid_string = get_geoid_grid_string(geoid_name)

    return (
        f"{horizontal_srs} "
        f"+vunits={vunits} "
        f"+geoidgrids={geoid_grid_string}"
    )


def convert_las_between_geoids(
    source_file: Path,
    target_reference_file: Path,
    output_file: Path,
    source_geoid: str,
    target_geoid: str,
):
    output_file.parent.mkdir(parents=True, exist_ok=True)

    if output_file.exists():
        output_file.unlink()

    source_geoid = normalize_geoid_name(source_geoid)
    target_geoid = normalize_geoid_name(target_geoid)

    source_crs = get_las_crs_object(source_file)
    target_crs = get_las_crs_object(target_reference_file)

    source_srs = build_srs(
        source_crs,
        source_geoid,
        SOURCE_VERTICAL_UNITS_OVERRIDE
    )

    target_srs = build_srs(
        target_crs,
        target_geoid,
        TARGET_VERTICAL_UNITS_OVERRIDE
    )

    target_wkt = target_crs.to_wkt()

    print("Source geoid:", source_geoid)
    print("Target geoid:", target_geoid)
    print("Source CRS from header:")
    print(" ", source_crs.name)
    print("Target CRS from reference:")
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
                "scale_x": LAS_SCALE_X,
                "scale_y": LAS_SCALE_Y,
                "scale_z": LAS_SCALE_Z,
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


# ============================================================
# COMPARISON / QA
# ============================================================

def compare_generated_to_reference(generated_file: Path, reference_file: Path, source_file: Path = None):
    with laspy.open(generated_file, read_evlrs=True) as gen_reader, laspy.open(reference_file, read_evlrs=True) as ref_reader:
        gen_h = gen_reader.header
        ref_h = ref_reader.header

        gen_crs = gen_h.parse_crs()
        ref_crs = ref_h.parse_crs()

        row = {
            "source_file": source_file.name if source_file else None,
            "generated_file": generated_file.name,
            "reference_file": reference_file.name,

            "source_geoid": normalize_geoid_name(SOURCE_GEOID),
            "target_geoid": normalize_geoid_name(TARGET_GEOID),

            "generated_crs": gen_crs.name if gen_crs else None,
            "reference_crs": ref_crs.name if ref_crs else None,
            "generated_matches_reference_crs": (gen_crs.name if gen_crs else None) == (ref_crs.name if ref_crs else None),

            "generated_point_count": int(gen_h.point_count),
            "reference_point_count": int(ref_h.point_count),
            "point_count_diff": int(gen_h.point_count) - int(ref_h.point_count),

            "generated_min_x": float(gen_h.mins[0]),
            "reference_min_x": float(ref_h.mins[0]),
            "min_x_diff": float(gen_h.mins[0] - ref_h.mins[0]),

            "generated_min_y": float(gen_h.mins[1]),
            "reference_min_y": float(ref_h.mins[1]),
            "min_y_diff": float(gen_h.mins[1] - ref_h.mins[1]),

            "generated_min_z": float(gen_h.mins[2]),
            "reference_min_z": float(ref_h.mins[2]),
            "min_z_diff": float(gen_h.mins[2] - ref_h.mins[2]),

            "generated_max_z": float(gen_h.maxs[2]),
            "reference_max_z": float(ref_h.maxs[2]),
            "max_z_diff": float(gen_h.maxs[2] - ref_h.maxs[2]),
        }

        if source_file is not None:
            row["source_point_count"] = get_point_count_simple(source_file)
            row["generated_minus_source_point_count"] = row["generated_point_count"] - row["source_point_count"]

        if gen_h.point_count != ref_h.point_count:
            row["point_by_point_status"] = "Skipped - point counts differ"
            return row

        dx_sum = dy_sum = dz_sum = 0.0
        dx_min = dy_min = dz_min = np.inf
        dx_max = dy_max = dz_max = -np.inf
        n_total = 0

        for gp, rp in zip(
            gen_reader.chunk_iterator(1_000_000),
            ref_reader.chunk_iterator(1_000_000)
        ):
            dx = np.asarray(gp.x) - np.asarray(rp.x)
            dy = np.asarray(gp.y) - np.asarray(rp.y)
            dz = np.asarray(gp.z) - np.asarray(rp.z)

            dx_min = min(dx_min, float(dx.min()))
            dx_max = max(dx_max, float(dx.max()))
            dx_sum += float(dx.sum())

            dy_min = min(dy_min, float(dy.min()))
            dy_max = max(dy_max, float(dy.max()))
            dy_sum += float(dy.sum())

            dz_min = min(dz_min, float(dz.min()))
            dz_max = max(dz_max, float(dz.max()))
            dz_sum += float(dz.sum())

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


# ============================================================
# MAIN
# ============================================================

def main():
    source_geoid = normalize_geoid_name(SOURCE_GEOID)
    target_geoid = normalize_geoid_name(TARGET_GEOID)

    print("PDAL used:", PDAL_EXE)
    print("Source folder:", SOURCE_FOLDER)
    print("Target/reference folder:", TARGET_REFERENCE_FOLDER)
    print("Output folder:", OUTPUT_FOLDER)
    print("Conversion:", source_geoid, "->", target_geoid)

    download_required_grid_files()

    source_files = list_las_files(SOURCE_FOLDER)
    reference_files = list_las_files(TARGET_REFERENCE_FOLDER)
    reference_lookup = make_reference_lookup(reference_files)

    print("\nSource count:", len(source_files))
    print("Reference count:", len(reference_files))

    if MAX_FILES_TO_PROCESS is not None:
        source_files = source_files[:MAX_FILES_TO_PROCESS]

    print(f"\nProcessing {len(source_files)} source files.")

    comparison_rows = []
    missing_reference = []

    for index, source_file in enumerate(source_files, start=1):
        reference_file = reference_lookup.get(source_file.name.lower())

        if reference_file is None:
            missing_reference.append(source_file.name)
            print(f"Missing reference file for: {source_file.name}")
            continue

        output_file = OUTPUT_FOLDER / f"{source_file.stem}_generated_{target_geoid.lower()}{OUTPUT_EXTENSION}"

        print("\n" + "=" * 80)
        print(f"Pair {index}")
        print(f"Source {source_geoid}:", source_file.name)
        print(f"Reference {target_geoid}:", reference_file.name)
        print("Output:", output_file.name)

        source_count = get_point_count_simple(source_file)
        reference_count = get_point_count_simple(reference_file)

        print("Source point count:", source_count)
        print("Reference point count:", reference_count)

        print("Converting...")
        convert_las_between_geoids(
            source_file=source_file,
            target_reference_file=reference_file,
            output_file=output_file,
            source_geoid=source_geoid,
            target_geoid=target_geoid,
        )

        generated_count = get_point_count_simple(output_file)
        print("Generated point count:", generated_count)
        print("Generated - Source point count:", generated_count - source_count)

        print("Output CRS check:")
        print(" ", get_crs_name(output_file))

        print("Comparing generated vs reference...")
        row = compare_generated_to_reference(output_file, reference_file, source_file)
        comparison_rows.append(row)

        write_comparison_csv(comparison_rows)

        print("Done with this file.")
        print("point_by_point_status:", row.get("point_by_point_status"))
        print("generated_matches_reference_crs:", row.get("generated_matches_reference_crs"))
        print("point_count_diff:", row.get("point_count_diff"))
        print("dz_mean:", row.get("dz_mean"))
        print("dx_mean:", row.get("dx_mean"))
        print("dy_mean:", row.get("dy_mean"))

    if missing_reference:
        missing_csv = OUTPUT_FOLDER / "missing_reference_files.csv"
        pd.DataFrame({"missing_reference_for_source_file": missing_reference}).to_csv(missing_csv, index=False)
        print("\nMissing reference file count:", len(missing_reference))
        print("Missing reference CSV:", missing_csv)

    print("\nAll requested files completed.")
    print("Final CSV:", OUTPUT_COMPARE_CSV)


if __name__ == "__main__":
    main()
