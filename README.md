Extract individual CR3 images from a Canon CR3 burst/roll file produced by Canon R7. 

Usage:
```
pip3 install -r requirements.txt
python3 extract_burst.py <path_to_your_burst_file.CR3>
```

The extracted files are not identical to those produced by DPP, however, they are likely to be functionally identical unless you want to view AF points or sensor calibration data ([comparison](https://github.com/AGFeldman/canon_burst_image_extract/blob/main/comparison.md)). Want to test that a sample extracted image works with your editing workflow? Here is a [sample image](https://aaron.na31.org/samples/202309070734-R3D_0806burst_1.CR3) extracted by this script.

Tested with:
* Input cRAW (compressed RAW) burst files from Canon R7
* Viewing/editing programs DxO PhotoLab 8.13.0, Digital Photo Professional 4.20.20.0, FastRawViewer 2.0.9, macOS 26.3

Not tested with:
* Uncompressed RAW files
* Canon R6ii
* Canon R8

Open an [issue](https://github.com/AGFeldman/canon_burst_image_extract/issues) if you have sample RAW burst files from R6ii or R8 to share, or if you want me to test with uncompressed RAW burst files.

The code in this repo is 100% AI-generated. Here is a 100% human-written blog post about it: https://aaron.na31.org/claude_cr3

AI code generation used [canon_cr3](https://github.com/lclevy/canon_cr3) as context. Therefore:

---

This repository contains a modified version of [canon_cr3](https://github.com/lclevy/canon_cr3), originally licensed under the GNU General Public License v3.0. Changes were made by Aaron Feldman on 2026-03-01 to produce the result described in this README. The full GPLv3 license is included in LICENSE.
