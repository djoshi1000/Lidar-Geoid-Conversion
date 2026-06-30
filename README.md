# LiDAR Geoid Conversion Toolkit

A Python-based toolkit for converting LiDAR point clouds and DEM rasters between NOAA geoid models and coordinate reference systems, with automated QA reporting.

This repository was developed for batch geoid/CRS conversion workflows such as:

- GEOID99 → GEOID12B
- GEOID12B → GEOID99
- GEOID18 → GEOID12B
- Other supported geoid-to-geoid conversions when the required PROJ/NOAA grid files are available

The tools are designed for production-style LiDAR QA, where converted outputs are compared against existing reference deliverables using CRS checks, point counts, bounding boxes, overlapping raster pixels, and elevation-difference statistics.

---

## Why This Repository Exists

Large LiDAR deliveries often exist in different horizontal coordinate systems, vertical units, and geoid models. A simple file copy or Z-offset is not enough when converting between products such as GEOID99, GEOID12B, and GEOID18.

This toolkit provides repeatable scripts to:

1. Read source CRS metadata from LAS/LAZ or raster headers.
2. Select source and target geoid grids automatically.
3. Convert point clouds or DEMs into a target/reference CRS.
4. Preserve or align outputs to the expected delivery format.
5. Generate QA reports after each conversion.

---

## Main Capabilities

| Tool | Input | Output | Main Purpose |
|---|---|---|---|
| `generic_las_geoid_converter.py` | `.las` / `.laz` | Converted `.las` | Convert LAS/LAZ point clouds between geoid models using PDAL + PROJ |
| `generic_dem_geoid_converter.py` | `.tif`, `.tiff`, `.img` | Converted GeoTIFF + QA CSV | Convert DEM rasters between geoid models, align to reference grid, and compare overlap pixels |
| QA CSV reports | Generated automatically | `.csv` | Track CRS match, point counts, overlap, elevation bias, MAE, RMSE, percentiles |

---

## Supported Geoid Models

The current scripts include a configurable geoid registry for:

| Geoid Model | PROJ/NOAA Grid File(s) |
|---|---|
| `GEOID99` | `us_noaa_g1999u01.tif` through `us_noaa_g1999u08.tif` |
| `GEOID12B` | `us_noaa_g2012bu0.tif` |
| `GEOID18` | `us_noaa_g2018u0.tif` |

The scripts download missing grid files automatically from the PROJ CDN when needed.

Additional geoid models can be added by editing the `GEOID_REGISTRY` dictionary in each script.

---

## Repository Structure

A recommended repository layout is:

```text
Lidar-Geoid-Conversion/
├── README.md
├── generic_las_geoid_converter.py
├── generic_dem_geoid_converter.py
├── requirements.txt
├── examples/
│   ├── example_las_config.txt
│   └── example_dem_config.txt
├── outputs/
│   ├── las_conversion_QA.csv
│   └── dem_geoid_conversion_QA.csv
└── docs/
    └── workflow_notes.md
```

The `outputs/` folder should normally be ignored by Git if it contains large generated files.

---

## Installation

The recommended setup is a Conda environment because PDAL, GDAL, Rasterio, PyProj, and LAS/LAZ compression libraries are easier to install from `conda-forge`.

```bash
conda create -n geoid-conversion -c conda-forge python=3.11 pdal laspy rasterio pyproj pandas numpy laszip lazrs -y
conda activate geoid-conversion
```

Check that PDAL is available:

```bash
pdal --version
```

For Jupyter Notebook usage:

```bash
python -m pip install ipykernel
python -m ipykernel install --user --name geoid-conversion --display-name "Python (geoid-conversion)"
```

---

## LAS/LAZ Geoid Conversion

### Script

```text
generic_las_geoid_converter.py
```

### What It Does

The LAS converter:

1. Reads source LAS/LAZ files from a source folder.
2. Reads matching reference LAS/LAZ files from a target/reference folder.
3. Reads CRS metadata from the LAS headers.
4. Builds the correct source and target geoid-based SRS strings.
5. Runs a PDAL reprojection pipeline.
6. Writes converted LAS files using the target/reference CRS WKT.
7. Generates a QA CSV after each file.

### Example: GEOID99 → GEOID12B

Edit only the user settings section:

```python
SOURCE_FOLDER = Path(
    r"C:\Users\Durga\Downloads\geoid\2018\New_test\Geoid_99"
)

TARGET_REFERENCE_FOLDER = Path(
    r"C:\Users\Durga\Downloads\geoid\2018\New_test\Geoid_12"
)

OUTPUT_FOLDER = Path(
    r"C:\Users\Durga\Downloads\geoid\2018\New_test\Generated_Geoid12B_from_Geoid99"
)

SOURCE_GEOID = "GEOID99"
TARGET_GEOID = "GEOID12B"
```

Run:

```bash
python generic_las_geoid_converter.py
```

### Example: GEOID12B → GEOID99

```python
SOURCE_GEOID = "GEOID12B"
TARGET_GEOID = "GEOID99"
```

The script automatically changes the grid assignment:

```text
source_srs = source CRS + source geoid grid
target_srs = target CRS + target geoid grid
```

No internal function rewrite is needed.

---

## DEM Geoid Conversion

### Script

```text
generic_dem_geoid_converter.py
```

### What It Does

The DEM converter:

1. Reads DEM rasters from a source folder.
2. Reads target/reference DEM rasters from a reference folder.
3. Finds overlapping rasters spatially, even when filenames or extents do not match.
4. Horizontally reprojects the source DEM onto the target/reference grid.
5. Applies geoid-to-geoid vertical conversion using PyProj/PROJ.
6. Saves converted DEM rasters.
7. Compares only valid overlapping pixels.
8. Writes a QA CSV with elevation-difference statistics.

### Example: GEOID99 → GEOID12B

```python
SOURCE_DEM_FOLDER = Path(
    r"C:\Users\Durga\Downloads\geoid\2018\Geoid99\Hydroflattened_DEM"
)

TARGET_REFERENCE_DEM_FOLDER = Path(
    r"C:\Users\Durga\Downloads\geoid\2018\Geoid\HydroFlattened_DEM"
)

OUTPUT_FOLDER = Path(
    r"C:\Users\Durga\Downloads\geoid\2018\Generated_DEM_Geoid12B_from_Geoid99"
)

SOURCE_GEOID = "GEOID99"
TARGET_GEOID = "GEOID12B"
```

Run:

```bash
python generic_dem_geoid_converter.py
```

### Important DEM Setting

For real geoid-to-geoid DEM conversion, keep:

```python
APPLY_VERTICAL_GEOID_TRANSFORM = True
```

If this is set to `False`, the script performs horizontal reprojection and Z-unit scaling only. That is useful for debugging, but it is not a full geoid conversion.

---

## QA Outputs

Each script creates a CSV report.

### LAS QA Columns

Typical LAS QA fields include:

| Column | Meaning |
|---|---|
| `source_file` | Input source LAS/LAZ |
| `generated_file` | Converted output LAS |
| `reference_file` | Existing target/reference LAS |
| `source_geoid` | Source geoid model |
| `target_geoid` | Target geoid model |
| `generated_crs` | CRS written to generated LAS |
| `reference_crs` | CRS from reference LAS |
| `generated_matches_reference_crs` | Whether output CRS matches reference CRS |
| `source_point_count` | Number of source points |
| `generated_point_count` | Number of generated points |
| `generated_minus_source_point_count` | Check that PDAL preserved points |
| `point_count_diff` | Generated minus reference point count |
| `dx_mean`, `dy_mean`, `dz_mean` | Row-wise coordinate differences when point counts match |

Important note: row-wise point comparison is only reliable when point order is identical. For production QA, nearest-neighbor spatial comparison is recommended when point order may differ.

### DEM QA Columns

Typical DEM QA fields include:

| Column | Meaning |
|---|---|
| `source_file` | Input DEM |
| `target_reference_file` | Existing reference DEM |
| `generated_raster` | Converted output raster |
| `source_input_crs` | CRS of source DEM |
| `generated_output_crs` | CRS of converted DEM |
| `target_existing_crs` | CRS of reference DEM |
| `generated_matches_target_crs` | Whether generated CRS matches reference CRS |
| `source_geoid` | Source geoid model |
| `target_geoid` | Target geoid model |
| `overlap_pct_target` | Percent of reference raster overlapped |
| `valid_overlap_pixels` | Number of valid pixels compared |
| `diff_mean` | Mean elevation difference |
| `diff_median` | Median elevation difference |
| `mae` | Mean absolute error |
| `rmse` | Root mean square error |
| `abs_diff_95pct` | 95th percentile absolute elevation difference |
| `abs_diff_99pct` | 99th percentile absolute elevation difference |

---

## Example Validation Results

During testing, the workflow successfully converted and compared LiDAR/DEM products between GEOID12B-style source data and existing GEOID99 reference data.

### LAS Trial

| QA Check | Result |
|---|---|
| Output CRS | Matched existing GEOID99 CRS |
| Horizontal nearest-neighbor mean difference | Approximately `0.0089 ft` |
| Vertical difference | Consistent systematic bias observed in LAS reference comparison |
| Interpretation | CRS/geoid transformation worked; reference LAS may include production-specific vertical offset or processing difference |

### DEM Trial

| QA Check | Result |
|---|---|
| Successful DEM overlap comparisons | `52 / 52` |
| Mean elevation difference | Approximately `-0.026 ft` |
| MAE | Approximately `0.085 ft` |
| RMSE | Approximately `0.14 ft` |
| Interpretation | DEM conversion and overlap comparison workflow performed well |

These values are example QA results from one test dataset. New projects should always be validated against their own reference data.

---

## Recommended Workflow

```text
1. Prepare source data folder
2. Prepare target/reference data folder
3. Set SOURCE_GEOID and TARGET_GEOID
4. Run a small test batch first
5. Review output CRS and point/pixel counts
6. Review QA CSV statistics
7. Visually inspect sample outputs in GIS
8. Scale up to full batch processing
```

For large projects, process data in batches and keep outputs on fast local NVMe storage when possible.

---

## Notes and Limitations

- The scripts assume input files contain valid CRS metadata.
- The LAS converter uses reference LAS files to define the expected output CRS/WKT.
- The DEM converter uses reference DEM rasters as the output grid template.
- DEM comparison is performed only where source and reference rasters overlap.
- Intensity rasters should not receive geoid vertical conversion because intensity values are not elevations.
- If generated LAS outputs are only a few KB, check source CRS assumptions and point counts immediately.
- Do not apply a fixed vertical offset unless QA proves a consistent production-specific bias.

---

## Troubleshooting

### Output LAS is only a few KB

This usually means the file contains only a header and zero points.

Check:

```python
with laspy.open(output_file, read_evlrs=True) as reader:
    print(reader.header.point_count)
```

Common causes:

- Source CRS is missing or incorrect.
- Source geoid model was set incorrectly.
- Target geoid model was set incorrectly.
- The wrong source/reference files were paired.

### `dz_mean`, `dx_mean`, or `dy_mean` is `None`

This usually means point-by-point QA was skipped because generated and reference point counts differ.

Check:

```text
generated_point_count
reference_point_count
point_count_diff
```

This does not always mean conversion failed. It may mean the reference LAS was clipped, filtered, tiled, or processed differently.

### DEM shows no overlap

Check:

- Source CRS
- Target/reference CRS
- Raster extents
- Whether the correct target/reference tile folder was used
- `MIN_OVERLAP_PERCENT_OF_TARGET`

---

## Batch Processing Guidance

For thousands of LiDAR tiles:

- Start with 2–5 test files.
- Confirm CRS, point counts, and QA metrics.
- Then run larger batches.
- Use fast local SSD/NVMe scratch space.
- Avoid running too many parallel jobs if disk I/O becomes saturated.
- Write QA CSV after each file so partial progress is preserved.

---

## Dependencies

Main Python and geospatial dependencies:

```text
python
pdal
laspy
rasterio
pyproj
numpy
pandas
laszip
lazrs
```

External geospatial engines used by the workflow:

```text
PDAL
PROJ
GDAL/Rasterio
```

---

## References

Useful project documentation:

- PDAL reprojection filter documentation
- PROJ coordinate transformation documentation
- Rasterio reprojection documentation
- Laspy LAS/LAZ I/O documentation
- NOAA/PROJ geoid grid resources

---

## Author

Developed by Durga Joshi for LiDAR geoid/CRS conversion and QA workflows.

---

## License

internal-use only.

Example:

```text
MIT License
```

---

## Disclaimer

This toolkit is intended for geospatial processing and QA support. Final production delivery should always be validated using project-specific control data, agency standards, and independent QA/QC procedures.
