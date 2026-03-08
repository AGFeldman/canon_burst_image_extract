#!/usr/bin/env python3
"""
Extract individual CR3 images from a Canon CR3 burst/roll file.

Canon's CR3 format uses the ISO Base Media File Format (ISOBMFF) container.
A burst CR3 file stores multiple images as multiple samples per track,
with sample tables (stsz/co64) indexing into a single mdat box.

This script parses the burst file structure, extracts per-image data from
each track, and constructs individual single-image CR3 files.
"""

import struct
import sys
import os
import io
from PIL import Image


# --- Low-level helpers ---

def read_u16_be(data, offset):
    return struct.unpack('>H', data[offset:offset+2])[0]

def read_u32_be(data, offset):
    return struct.unpack('>I', data[offset:offset+4])[0]

def read_u64_be(data, offset):
    return struct.unpack('>Q', data[offset:offset+8])[0]

def read_u16_le(data, offset):
    return struct.unpack('<H', data[offset:offset+2])[0]

def read_u32_le(data, offset):
    return struct.unpack('<I', data[offset:offset+4])[0]

def pack_u16_be(val):
    return struct.pack('>H', val)

def pack_u32_be(val):
    return struct.pack('>I', val)

def pack_u64_be(val):
    return struct.pack('>Q', val)

def pack_u16_le(val):
    return struct.pack('<H', val)

def pack_u32_le(val):
    return struct.pack('<I', val)


def make_box(box_type, content):
    """Create a standard ISOBMFF box."""
    if isinstance(box_type, str):
        box_type = box_type.encode('ascii')
    size = 8 + len(content)
    if size > 0xFFFFFFFF:
        # Need extended size
        return pack_u32_be(1) + box_type + pack_u64_be(16 + len(content)) + content
    return pack_u32_be(size) + box_type + content


def make_fullbox(box_type, version, flags, content):
    """Create a full box (with version and flags)."""
    vf = pack_u32_be((version << 24) | flags)
    return make_box(box_type, vf + content)


def make_uuid_box(uuid_bytes, content):
    """Create a UUID box."""
    size = 8 + 16 + len(content)
    if size > 0xFFFFFFFF:
        return pack_u32_be(1) + b'uuid' + pack_u64_be(24 + len(content)) + uuid_bytes + content
    return pack_u32_be(size) + b'uuid' + uuid_bytes + content


# --- ISOBMFF Box Parser ---

def parse_top_level_boxes(data):
    """Parse top-level boxes from ISOBMFF data. Returns list of (offset, size, type, header_size)."""
    boxes = []
    pos = 0
    while pos < len(data) - 8:
        size = read_u32_be(data, pos)
        box_type = data[pos+4:pos+8]
        if size == 1:
            size = read_u64_be(data, pos+8)
            header_size = 16
        elif size == 0:
            size = len(data) - pos
            header_size = 8
        else:
            header_size = 8
        if size < 8:
            break
        boxes.append((pos, size, box_type, header_size))
        pos += size
        if pos > len(data):
            break
    return boxes


def find_box(data, start, end, target_type):
    """Find a box of given type within a range. Returns (offset, size, header_size) or None."""
    pos = start
    while pos < end - 8:
        size = read_u32_be(data, pos)
        box_type = data[pos+4:pos+8]
        if size == 1:
            size = read_u64_be(data, pos+8)
            header_size = 16
        elif size == 0:
            size = end - pos
            header_size = 8
        else:
            header_size = 8
        if size < 8 or pos + size > end:
            break
        if isinstance(target_type, str):
            target_type = target_type.encode('ascii')
        if box_type == target_type:
            return (pos, size, header_size)
        pos += size
    return None


def find_all_boxes(data, start, end):
    """Find all boxes within a range."""
    boxes = []
    pos = start
    while pos < end - 8:
        size = read_u32_be(data, pos)
        box_type = data[pos+4:pos+8]
        if size == 1:
            size = read_u64_be(data, pos+8)
            header_size = 16
        elif size == 0:
            size = end - pos
            header_size = 8
        else:
            header_size = 8
        if size < 8 or pos + size > end:
            break
        boxes.append((pos, size, box_type, header_size))
        pos += size
    return boxes


# --- Canon CR3 specific parsing ---

class CR3BurstFile:
    """Parse and extract from a Canon CR3 burst file."""

    # Known UUID values
    UUID_CANON = bytes.fromhex('85c0b687820f11e08111f4ce462b6a48')
    UUID_XMP = bytes.fromhex('be7acfcb97a942e89c71999491e3afac')
    UUID_PRVW = bytes.fromhex('eaf42b5e1c984b88b9fbb7dc406e4d16')
    UUID_CMTA = bytes.fromhex('5766b829bb6a47c5bcfb8b9f2260d06d')
    UUID_CNOP = bytes.fromhex('210f1687914911e4811100242131fce4')

    def __init__(self, filepath):
        with open(filepath, 'rb') as f:
            self.data = f.read()
        self.filepath = filepath
        self._parse()

    def _parse(self):
        """Parse the burst CR3 file."""
        top_boxes = parse_top_level_boxes(self.data)

        self.ftyp = None
        self.moov_offset = None
        self.moov_size = None
        self.xmp_box = None
        self.prvw_box = None
        self.cmta_box = None
        self.free_box = None
        self.mdat_offset = None
        self.mdat_size = None
        self.mdat_header_size = None

        for offset, size, box_type, hs in top_boxes:
            if box_type == b'ftyp':
                self.ftyp = self.data[offset:offset+size]
            elif box_type == b'moov':
                self.moov_offset = offset
                self.moov_size = size
            elif box_type == b'uuid':
                uuid_val = self.data[offset+8:offset+24]
                if uuid_val == self.UUID_XMP:
                    self.xmp_box = self.data[offset:offset+size]
                elif uuid_val == self.UUID_PRVW:
                    self.prvw_offset = offset
                    self.prvw_size = size
                elif uuid_val == self.UUID_CMTA:
                    self.cmta_box = self.data[offset:offset+size]
            elif box_type == b'free':
                self.free_box = self.data[offset:offset+size]
            elif box_type == b'mdat':
                self.mdat_offset = offset
                self.mdat_size = size
                self.mdat_header_size = hs

        self._parse_moov()
        self.exposure_data = self._parse_ctmd_exposure()

    def _parse_moov(self):
        """Parse the moov box to extract track info and Canon metadata."""
        moov_start = self.moov_offset + 8  # skip moov box header
        moov_end = self.moov_offset + self.moov_size

        # Find all top-level boxes in moov
        moov_boxes = find_all_boxes(self.data, moov_start, moov_end)

        self.tracks = []
        self.canon_uuid_raw = None
        self.mvhd_raw = None
        self.canon_sub_boxes = []

        for offset, size, box_type, hs in moov_boxes:
            if box_type == b'uuid':
                uuid_val = self.data[offset+8:offset+24]
                if uuid_val == self.UUID_CANON:
                    self.canon_uuid_offset = offset
                    self.canon_uuid_size = size
                    self._parse_canon_uuid(offset, size)
            elif box_type == b'mvhd':
                self.mvhd_raw = self.data[offset:offset+size]
            elif box_type == b'trak':
                self._parse_trak(offset, size)

    def _parse_canon_uuid(self, offset, size):
        """Parse Canon-specific UUID box inside moov."""
        content_start = offset + 24  # box header (8) + UUID (16)
        content_end = offset + size

        self.canon_sub_boxes = []
        pos = content_start
        while pos < content_end - 8:
            sub_size = read_u32_be(self.data, pos)
            sub_type = self.data[pos+4:pos+8]
            if sub_size < 8 or pos + sub_size > content_end:
                break
            self.canon_sub_boxes.append({
                'type': sub_type,
                'offset': pos,
                'size': sub_size,
                'data': self.data[pos:pos+sub_size],
            })
            pos += sub_size

    def _parse_trak(self, trak_offset, trak_size):
        """Parse a track box."""
        track = {
            'offset': trak_offset,
            'size': trak_size,
            'handler': None,
            'stsz_sizes': [],
            'stsz_fixed': False,
            'co64_offsets': [],
            'stsd_raw': None,
            'tkhd_raw': None,
            'mdhd_raw': None,
            'hdlr_raw': None,
            'vmhd_raw': None,
            'nmhd_raw': None,
            'dinf_raw': None,
            'stts_raw': None,
            'stsc_raw': None,
        }
        self._find_track_boxes(trak_offset + 8, trak_offset + trak_size, track)
        self.tracks.append(track)

    def _find_track_boxes(self, start, end, track):
        """Recursively find relevant boxes in a track."""
        pos = start
        while pos < end - 8:
            size = read_u32_be(self.data, pos)
            box_type = self.data[pos+4:pos+8]
            if size < 8 or pos + size > end:
                break
            type_str = box_type.decode('ascii', errors='replace')

            if box_type == b'tkhd':
                track['tkhd_raw'] = self.data[pos:pos+size]
            elif box_type == b'mdhd':
                track['mdhd_raw'] = self.data[pos:pos+size]
            elif box_type == b'hdlr':
                track['hdlr_raw'] = self.data[pos:pos+size]
                if size > 20:
                    track['handler'] = self.data[pos+16:pos+20].decode('ascii', errors='replace')
            elif box_type == b'vmhd':
                track['vmhd_raw'] = self.data[pos:pos+size]
            elif box_type == b'nmhd':
                track['nmhd_raw'] = self.data[pos:pos+size]
            elif box_type == b'dinf':
                track['dinf_raw'] = self.data[pos:pos+size]
            elif box_type == b'stsd':
                track['stsd_raw'] = self.data[pos:pos+size]
            elif box_type == b'stts':
                track['stts_raw'] = self.data[pos:pos+size]
            elif box_type == b'stsc':
                track['stsc_raw'] = self.data[pos:pos+size]
            elif box_type == b'stsz':
                self._parse_stsz(pos, size, track)
            elif box_type == b'co64':
                self._parse_co64(pos, size, track)
            elif box_type in (b'mdia', b'minf', b'stbl'):
                self._find_track_boxes(pos + 8, pos + size, track)

            pos += size

    def _parse_stsz(self, offset, size, track):
        """Parse sample size box."""
        sample_size = read_u32_be(self.data, offset + 12)
        count = read_u32_be(self.data, offset + 16)
        if sample_size != 0:
            track['stsz_sizes'] = [sample_size] * count
            track['stsz_fixed'] = True
        else:
            sizes = []
            for i in range(count):
                sizes.append(read_u32_be(self.data, offset + 20 + i * 4))
            track['stsz_sizes'] = sizes
            track['stsz_fixed'] = False

    def _parse_co64(self, offset, size, track):
        """Parse chunk offset box (64-bit)."""
        count = read_u32_be(self.data, offset + 12)
        offsets = []
        for i in range(count):
            offsets.append(read_u64_be(self.data, offset + 16 + i * 8))
        track['co64_offsets'] = offsets

    def _parse_ctmd_exposure(self):
        """Parse per-image exposure data from Track 4 CTMD records.

        CTMD records are self-describing: each has a 12-byte little-endian
        header (size, type, unk). We walk through records to find type 5
        which contains exposure info (aperture, shutter, ISO).
        Returns list of dicts with 'iso' key per image.
        """
        if len(self.tracks) < 4:
            return []
        track4 = self.tracks[3]  # Track 4 (0-indexed)
        exposure_data = []
        for i in range(self.num_images):
            offset = track4['co64_offsets'][i]
            size = track4['stsz_sizes'][i]
            record_data = self.data[offset:offset+size]
            exposure = {}
            pos = 0
            while pos < size - 12:
                rec_size, rec_type = struct.unpack('<II', record_data[pos:pos+8])
                if rec_size < 12 or pos + rec_size > size:
                    break
                if rec_type == 5 and rec_size >= 24:
                    # 12-byte record header, then little-endian exposure struct
                    f_num, f_denom, expo_num, expo_denom, iso = struct.unpack(
                        '<HHHHL', record_data[pos+12:pos+24])
                    exposure['iso'] = iso
                pos += rec_size
            exposure_data.append(exposure)
        return exposure_data

    @property
    def num_images(self):
        if self.tracks:
            return len(self.tracks[0]['co64_offsets'])
        return 0

    def _patch_cmt3_for_image(self, cmt3_data, image_index):
        """Patch CMT3 box data for a single image.

        - Tag 0x403F (count=3 LONGs): set index 2 from num_images to 1
        - Tag 0x4040 (count=10 LONGs): set index 1 from 0 to 1, index 9 from 1 to 3
        """
        cmt3 = bytearray(cmt3_data)
        tiff_start = 8  # skip box header

        is_le = (cmt3[tiff_start:tiff_start+2] == b'II')
        fmt16 = '<H' if is_le else '>H'
        fmt32 = '<I' if is_le else '>I'

        ifd_offset = struct.unpack(fmt32, cmt3[tiff_start+4:tiff_start+8])[0]
        ifd_abs = tiff_start + ifd_offset
        entry_count = struct.unpack(fmt16, cmt3[ifd_abs:ifd_abs+2])[0]

        for j in range(entry_count):
            entry_off = ifd_abs + 2 + j * 12
            tag = struct.unpack(fmt16, cmt3[entry_off:entry_off+2])[0]
            typ = struct.unpack(fmt16, cmt3[entry_off+2:entry_off+4])[0]
            count = struct.unpack(fmt32, cmt3[entry_off+4:entry_off+8])[0]
            value_offset = struct.unpack(fmt32, cmt3[entry_off+8:entry_off+12])[0]

            if tag == 0x403F and count == 3 and typ == 4:
                # Out-of-line LONG array
                base = tiff_start + value_offset
                # Change index 1 to 0 (burst stores frame-related value, single = 0)
                cmt3[base + 1*4:base + 1*4 + 4] = struct.pack(fmt32, 0)
                # Change index 2 from num_images to 1
                cmt3[base + 2*4:base + 2*4 + 4] = struct.pack(fmt32, 1)
            elif tag == 0x4040 and count == 10 and typ == 4:
                # Out-of-line LONG array
                base = tiff_start + value_offset
                # Change index 1 from 0 to 1
                cmt3[base + 1*4:base + 1*4 + 4] = struct.pack(fmt32, 1)
                # Change index 9 from 1 to 3
                cmt3[base + 9*4:base + 9*4 + 4] = struct.pack(fmt32, 3)

        return bytes(cmt3)

    def _patch_cmt2_for_image(self, cmt2_data, image_index):
        """Patch CMT2 box data for a single image.

        - Update ISO tags (0x8827, 0x8832) from CTMD per-image exposure data
        - Insert tag 0xA404 (DigitalZoomRatio) as RATIONAL 65535/65535

        Uses reassemble approach: parse TIFF IFD into parts, modify, recombine.
        Total box size must remain exactly the same (1544 bytes).
        """
        original_size = len(cmt2_data)
        tiff_start = 8  # skip box header
        tiff = bytearray(cmt2_data[tiff_start:])

        is_le = (tiff[0:2] == b'II')
        fmt16 = '<H' if is_le else '>H'
        fmt32 = '<I' if is_le else '>I'

        ifd_offset = struct.unpack(fmt32, tiff[4:8])[0]
        entry_count = struct.unpack(fmt16, tiff[ifd_offset:ifd_offset+2])[0]

        # Get per-image ISO
        iso_value = 0
        if self.exposure_data and image_index < len(self.exposure_data):
            iso_value = self.exposure_data[image_index].get('iso', 0)

        # TIFF type sizes for determining inline vs out-of-line
        type_sizes = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 9: 4, 10: 8}

        # Parse existing IFD entries
        entries_start = ifd_offset + 2
        parsed_entries = []
        for j in range(entry_count):
            off = entries_start + j * 12
            tag = struct.unpack(fmt16, tiff[off:off+2])[0]
            typ = struct.unpack(fmt16, tiff[off+2:off+4])[0]
            count = struct.unpack(fmt32, tiff[off+4:off+8])[0]
            value_raw = bytes(tiff[off+8:off+12])
            parsed_entries.append([tag, typ, count, value_raw])

        # Extract structural parts
        next_ifd_off = entries_start + entry_count * 12
        next_ifd_ptr = bytes(tiff[next_ifd_off:next_ifd_off+4])
        data_area_start = next_ifd_off + 4
        data_area = bytearray(tiff[data_area_start:])

        # Verify 20 bytes of zero padding at end (12 consumed by IFD growth + 8 for RATIONAL)
        assert data_area[-20:] == b'\x00' * 20, \
            f"Expected 20 bytes of zero padding at end of CMT2 data area"

        # New IFD has one more entry: data area starts 12 bytes later
        offset_shift = 12
        new_data_area_start = data_area_start + offset_shift

        # Trim 12 bytes from end of data area (was zero padding)
        new_data_area = data_area[:-12]

        # Write RATIONAL 65535/65535 into last 8 bytes of new data area
        rational_bytes = struct.pack(fmt32, 65535) + struct.pack(fmt32, 65535)
        new_data_area[-8:] = rational_bytes
        rational_tiff_offset = new_data_area_start + len(new_data_area) - 8

        # Adjust out-of-line offsets (+12) and update ISO tags
        for entry in parsed_entries:
            tag, typ, count, value_raw = entry
            elem_size = type_sizes.get(typ, 1)
            data_size = elem_size * count

            # Shift out-of-line offsets
            if data_size > 4:
                old_offset = struct.unpack(fmt32, value_raw)[0]
                entry[3] = struct.pack(fmt32, old_offset + offset_shift)

            # Update ISO tags (inline values, no offset shift needed)
            if tag == 0x8827 and iso_value:  # ISOSpeedRatings (inline SHORT)
                entry[3] = struct.pack(fmt16, iso_value) + b'\x00\x00'
            elif tag == 0x8832 and iso_value:  # ISOSpeed (inline LONG)
                entry[3] = struct.pack(fmt32, iso_value)

        # Insert new entry for 0xA404 (DigitalZoomRatio)
        # Type 5 = RATIONAL, count = 1, offset points to 65535/65535 data
        new_entry = [0xA404, 5, 1, struct.pack(fmt32, rational_tiff_offset)]
        parsed_entries.append(new_entry)

        # Sort by tag number (TIFF IFD requirement)
        parsed_entries.sort(key=lambda e: e[0])

        # Reassemble TIFF blob
        header = bytes(tiff[:ifd_offset])  # TIFF header (byte order + magic + IFD offset)
        new_count = struct.pack(fmt16, len(parsed_entries))

        new_entries = b''
        for tag, typ, count, value_raw in parsed_entries:
            new_entries += struct.pack(fmt16, tag)
            new_entries += struct.pack(fmt16, typ)
            new_entries += struct.pack(fmt32, count)
            new_entries += value_raw

        new_tiff = header + new_count + new_entries + next_ifd_ptr + bytes(new_data_area)

        result = cmt2_data[:8] + new_tiff  # box header + new TIFF
        assert len(result) == original_size, \
            f"CMT2 size changed: {len(result)} != {original_size}"
        return result

    def _build_stbl(self, track, image_index, co64_offset):
        """Build an stbl box for a single sample."""
        # stsd: copy from burst (codec info is the same)
        stsd = track['stsd_raw']

        # stts: 1 entry, count=1, delta=1
        stts = make_fullbox('stts', 0, 0,
            pack_u32_be(1) +  # entry count
            pack_u32_be(1) +  # sample count
            pack_u32_be(1)    # sample delta
        )

        # stsc: 1 entry (first_chunk=1, samples_per_chunk=1, sample_desc_index=1)
        stsc = make_fullbox('stsc', 0, 0,
            pack_u32_be(1) +  # entry count
            pack_u32_be(1) +  # first chunk
            pack_u32_be(1) +  # samples per chunk
            pack_u32_be(1)    # sample description index
        )

        # stsz: single sample
        sample_size = track['stsz_sizes'][image_index]
        if track['stsz_fixed']:
            stsz = make_fullbox('stsz', 0, 0,
                pack_u32_be(sample_size) +  # fixed sample size
                pack_u32_be(1)              # sample count
            )
        else:
            stsz = make_fullbox('stsz', 0, 0,
                pack_u32_be(sample_size) +  # fixed sample size (using it since only 1 sample)
                pack_u32_be(1)              # sample count
            )

        # co64: single offset
        co64 = make_fullbox('co64', 0, 0,
            pack_u32_be(1) +          # entry count
            pack_u64_be(co64_offset)  # chunk offset
        )

        return make_box('stbl', stsd + stts + stsc + stsz + co64)

    def _patch_tkhd_single(self, tkhd_raw):
        """Patch tkhd for a single-frame file: set duration=1."""
        tkhd = bytearray(tkhd_raw)
        version = tkhd[8]
        if version == 0:
            # version 0: modification_time at +16 (4 bytes), duration at +28 (4 bytes)
            # Duration: convert from burst value to 1
            tkhd[28:32] = pack_u32_be(1)
        return bytes(tkhd)

    def _patch_mdhd_single(self, mdhd_raw):
        """Patch mdhd for a single-frame file: set timescale=1, duration=1."""
        mdhd = bytearray(mdhd_raw)
        version = mdhd[8]
        if version == 0:
            # version 0: timescale at +20 (4 bytes), duration at +24 (4 bytes)
            mdhd[20:24] = pack_u32_be(1)  # timescale = 1
            mdhd[24:28] = pack_u32_be(1)  # duration = 1
        return bytes(mdhd)

    def _build_trak(self, track_index, image_index, co64_offset):
        """Build a trak box for a single image."""
        track = self.tracks[track_index]

        # Build stbl
        stbl = self._build_stbl(track, image_index, co64_offset)

        # Build minf
        if track['handler'] == 'vide':
            media_header = track['vmhd_raw']
        else:
            media_header = track['nmhd_raw']

        minf = make_box('minf', media_header + track['dinf_raw'] + stbl)

        # Patch mdhd for single-frame
        mdhd = self._patch_mdhd_single(track['mdhd_raw'])

        # Build mdia
        mdia = make_box('mdia', mdhd + track['hdlr_raw'] + minf)

        # Patch tkhd for single-frame
        tkhd = self._patch_tkhd_single(track['tkhd_raw'])

        # Build trak
        return make_box('trak', tkhd + mdia)

    def _reencode_thmb(self, thmb_data):
        """Re-encode the THMB box JPEG at DPP quality and rebuild the box.

        THMB layout: size(4) + 'THMB'(4) + version(4) + width(2) + height(2)
                     + jpeg_size(4) + unk(4) + jpeg_data
        """
        old_jpeg_size = read_u32_be(thmb_data, 16)
        old_jpeg = thmb_data[24:24 + old_jpeg_size]

        new_jpeg = self._reencode_jpeg(old_jpeg)

        # Rebuild: keep version/width/height, update jpeg_size, keep unk field
        content = (
            thmb_data[8:16] +                # version(4) + width(2) + height(2)
            pack_u32_be(len(new_jpeg)) +      # jpeg_size
            thmb_data[20:24] +                # unk field (4 bytes)
            new_jpeg
        )
        return make_box('THMB', content)

    @staticmethod
    def _reencode_jpeg(jpeg_data, quality=70):
        """Re-encode JPEG data at a target quality level.

        DPP uses standard IJG quantization tables at quality 70 with a
        specific JPEG structure: no JFIF/APP0, combined DQT and DHT segments.
        We re-encode with Pillow then post-process to match.
        """
        img = Image.open(io.BytesIO(jpeg_data))
        buf = io.BytesIO()
        img.save(buf, 'JPEG', quality=quality, subsampling='4:2:2')
        raw = buf.getvalue()

        # Post-process to match DPP's JPEG structure:
        # - Strip APP0 (JFIF) marker
        # - Combine multiple DQT segments into one
        # - Combine multiple DHT segments into one
        markers = []  # (marker_byte, payload_bytes) -- excludes SOI/EOI/SOS
        scan_data = b''
        pos = 2  # skip SOI (FFD8)
        while pos < len(raw) - 1:
            if raw[pos] != 0xFF:
                break
            marker = raw[pos + 1]
            if marker == 0xDA:  # SOS — rest is scan data
                seg_len = struct.unpack('>H', raw[pos+2:pos+4])[0]
                scan_data = raw[pos:]
                break
            if marker in (0x00, 0xFF):
                pos += 1
                continue
            seg_len = struct.unpack('>H', raw[pos+2:pos+4])[0]
            payload = raw[pos+4:pos+2+seg_len]
            if marker != 0xE0:  # skip APP0 (JFIF)
                markers.append((marker, payload))
            pos += 2 + seg_len

        # Combine DQT segments
        dqt_payloads = b''
        dht_payloads = b''
        result = b'\xff\xd8'
        for marker, payload in markers:
            if marker == 0xDB:  # DQT
                dqt_payloads += payload
            elif marker == 0xC4:  # DHT
                dht_payloads += payload
            else:
                # Flush DQT before SOF
                if marker in (0xC0, 0xC1, 0xC2) and dqt_payloads:
                    seg_len = 2 + len(dqt_payloads)
                    result += b'\xff\xdb' + struct.pack('>H', seg_len) + dqt_payloads
                    dqt_payloads = b''
                result += b'\xff' + bytes([marker])
                result += struct.pack('>H', 2 + len(payload)) + payload

        # Flush DHT before SOS
        if dht_payloads:
            seg_len = 2 + len(dht_payloads)
            result += b'\xff\xc4' + struct.pack('>H', seg_len) + dht_payloads

        result += scan_data
        return result

    def _build_canon_uuid(self, image_index):
        """Build the Canon-specific UUID box for a single image."""
        content = b''
        for sub in self.canon_sub_boxes:
            if sub['type'] == b'CCTP':
                # Modify CCTP flags: change from 2 (multi-frame) to 1 (single)
                cctp = bytearray(sub['data'])
                cctp[8+7] = 0x01  # flags byte: burst=0x02, single=0x01
                content += bytes(cctp)
            elif sub['type'] == b'CTBO':
                # Will be filled with placeholders, updated later
                content += sub['data']  # placeholder - will be patched
            elif sub['type'] == b'CMT2':
                content += self._patch_cmt2_for_image(sub['data'], image_index)
            elif sub['type'] == b'CMT3':
                content += self._patch_cmt3_for_image(sub['data'], image_index)
            elif sub['type'] == b'THMB':
                content += self._reencode_thmb(sub['data'])
            else:
                content += sub['data']

        return make_uuid_box(self.UUID_CANON, content)

    def _build_prvw_uuid(self, jpeg_data):
        """Build the PRVW UUID box from JPEG data."""
        # PRVW structure:
        # 8 bytes prefix (00000000 00000001)
        # PRVW sub-box: size(4) + "PRVW"(4) + version(4) + unk(2) + width(2) + height(2) + unk(2) + jpeg_size(4) + jpeg_data
        prvw_header = struct.pack('>IHHHHHI',
            0,             # version
            1,             # unknown (always 1)
            0x0654,        # width = 1620
            0x0438,        # height = 1080
            1,             # unknown
            0,             # padding? (this may not be right)
            len(jpeg_data) # jpeg size
        )
        # Actually let me look at the real structure more carefully
        # From analysis: content after uuid header is:
        # 8 bytes: 0000000000000001
        # Then PRVW sub-box
        prefix = pack_u32_be(0) + pack_u32_be(1)  # 8-byte prefix

        # PRVW sub-box content: version(4) + unk_short(2) + width(2) + height(2) + unk_short(2) + jpeg_size(4) + jpeg
        prvw_content = (
            pack_u32_be(0) +           # version
            pack_u16_be(1) +           # unknown
            pack_u16_be(0x0654) +      # width = 1620
            pack_u16_be(0x0438) +      # height = 1080
            pack_u16_be(1) +           # unknown
            pack_u32_be(len(jpeg_data)) +  # jpeg size
            jpeg_data
        )
        prvw_sub = make_box('PRVW', prvw_content)

        return make_uuid_box(self.UUID_PRVW, prefix + prvw_sub)

    def _build_mvhd_single(self):
        """Build mvhd for a single-frame file."""
        mvhd = bytearray(self.mvhd_raw)
        version = mvhd[8]
        if version == 0:
            # version 0 layout (after 8-byte box header + 4-byte version/flags):
            # +12: creation_time (4 bytes) - keep as-is
            # +16: modification_time (4 bytes) - keep as-is (can't predict DPP timestamp)
            # +20: timescale (4 bytes) - change from 30000 to 1
            # +24: duration (4 bytes) - change from 6006 to 1
            mvhd[20:24] = pack_u32_be(1)   # timescale = 1
            mvhd[24:28] = pack_u32_be(1)   # duration = 1
        return bytes(mvhd)

    def _build_moov(self, image_index, co64_offsets):
        """Build the complete moov box for a single image."""
        canon_uuid = self._build_canon_uuid(image_index)
        mvhd = self._build_mvhd_single()

        traks = b''
        for i in range(len(self.tracks)):
            traks += self._build_trak(i, image_index, co64_offsets[i])

        return make_box('moov', canon_uuid + mvhd + traks)

    def _patch_ctbo(self, moov_data, ctbo_entries):
        """Patch CTBO entries in the moov data.

        ctbo_entries: dict mapping entry_index to (offset, size)
        """
        moov = bytearray(moov_data)

        # Find CTBO box type signature in moov
        idx = moov.find(b'CTBO')
        if idx < 4:
            return bytes(moov)
        pos = idx - 4  # box starts 4 bytes before the type field

        # CTBO layout: size(4) + 'CTBO'(4) + count(4) + entries(count * 20)
        # Each entry: index(4) + offset(8) + size(8)
        count = read_u32_be(bytes(moov), pos + 8)
        for i in range(count):
            entry_offset = pos + 12 + i * 20
            entry_idx = read_u32_be(bytes(moov), entry_offset)
            if entry_idx in ctbo_entries:
                new_offset, new_size = ctbo_entries[entry_idx]
                moov[entry_offset+4:entry_offset+12] = pack_u64_be(new_offset)
                moov[entry_offset+12:entry_offset+20] = pack_u64_be(new_size)

        return bytes(moov)

    def extract_image(self, image_index, output_path):
        """Extract a single image from the burst file."""
        n = self.num_images
        if image_index < 0 or image_index >= n:
            raise ValueError(f"Image index {image_index} out of range (0 to {n-1})")

        # 1. Collect per-image track data
        track_data = []
        for t in self.tracks:
            offset = t['co64_offsets'][image_index]
            size = t['stsz_sizes'][image_index]
            track_data.append(self.data[offset:offset+size])

        # 2. Build output file layout:
        # ftyp | moov | uuid(XMP) | uuid(PRVW) | uuid(CMTA) | free | mdat

        ftyp = self.ftyp
        xmp_uuid = self.xmp_box

        # Build PRVW from this image's Track 1 JPEG, re-encoded at DPP quality
        prvw_jpeg = self._reencode_jpeg(track_data[0])
        prvw_uuid = self._build_prvw_uuid(prvw_jpeg)

        cmta_uuid = self.cmta_box
        free_box = self.free_box

        # Build mdat content: track data laid out sequentially
        mdat_content = b''.join(track_data)

        # Build moov with placeholder co64 values first to calculate sizes
        placeholder_offsets = [0] * len(self.tracks)
        moov = self._build_moov(image_index, placeholder_offsets)

        # Calculate the position where mdat data starts
        pre_mdat_size = (len(ftyp) + len(moov) + len(xmp_uuid) +
                        len(prvw_uuid) + len(cmta_uuid) + len(free_box))

        # Determine mdat box header size
        mdat_box_size = 8 + len(mdat_content)
        if mdat_box_size > 0xFFFFFFFF:
            mdat_header_size = 16
        else:
            mdat_header_size = 8

        mdat_data_start = pre_mdat_size + mdat_header_size

        # Calculate actual co64 offsets (absolute file positions)
        co64_offsets = []
        data_pos = mdat_data_start
        for i in range(len(self.tracks)):
            co64_offsets.append(data_pos)
            data_pos += len(track_data[i])

        # Rebuild moov with correct co64 values
        moov = self._build_moov(image_index, co64_offsets)
        assert len(moov) == len(self._build_moov(image_index, placeholder_offsets))

        # Patch CTBO entries
        xmp_offset = len(ftyp) + len(moov)
        prvw_offset = xmp_offset + len(xmp_uuid)
        cmta_offset = prvw_offset + len(prvw_uuid)
        mdat_offset = pre_mdat_size

        ctbo_entries = {
            1: (xmp_offset, len(xmp_uuid)),
            2: (prvw_offset, len(prvw_uuid)),
            3: (mdat_offset, mdat_box_size),
            4: (0, 0),  # unused
            5: (cmta_offset, len(cmta_uuid)),
        }
        moov = self._patch_ctbo(moov, ctbo_entries)

        # Build mdat box
        if mdat_box_size > 0xFFFFFFFF:
            mdat_box = pack_u32_be(1) + b'mdat' + pack_u64_be(16 + len(mdat_content)) + mdat_content
        else:
            mdat_box = pack_u32_be(mdat_box_size) + b'mdat' + mdat_content

        # 3. Write output file
        with open(output_path, 'wb') as f:
            f.write(ftyp)
            f.write(moov)
            f.write(xmp_uuid)
            f.write(prvw_uuid)
            f.write(cmta_uuid)
            f.write(free_box)
            f.write(mdat_box)

        total_size = (len(ftyp) + len(moov) + len(xmp_uuid) +
                     len(prvw_uuid) + len(cmta_uuid) + len(free_box) + len(mdat_box))
        return total_size


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <burst_cr3_file> [output_dir]")
        print("  Extracts individual CR3 images from a Canon CR3 burst/roll file.")
        sys.exit(1)

    input_path = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else os.path.dirname(input_path) or '.'

    # Handle hex dump files (xxd format) by detecting and converting
    with open(input_path, 'rb') as f:
        first_bytes = f.read(16)

    if first_bytes[:8] == b'00000000':
        # This looks like an xxd hex dump - convert to binary first
        print(f"Detected xxd hex dump format, converting to binary...")
        binary_data = bytearray()
        with open(input_path, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or ':' not in line:
                    continue
                hex_part = line.split(':')[1].split('  ')[0].strip()
                hex_bytes = hex_part.replace(' ', '')
                binary_data.extend(bytes.fromhex(hex_bytes))

        # Write temporary binary file
        temp_path = input_path + '.bin'
        with open(temp_path, 'wb') as f:
            f.write(binary_data)
        input_path = temp_path
        cleanup_temp = True
    else:
        cleanup_temp = False

    print(f"Parsing {input_path}...")
    cr3 = CR3BurstFile(input_path)
    n = cr3.num_images
    print(f"Found {n} images in burst file")

    if n <= 1:
        print("This doesn't appear to be a burst file (only 1 or 0 images found).")
        if cleanup_temp:
            os.unlink(input_path)
        sys.exit(1)

    # Generate output filenames based on input filename
    base_name = os.path.basename(sys.argv[1])  # use original name
    # Remove .CR3 extension
    name_without_ext = base_name
    for ext in ('.CR3', '.cr3'):
        if name_without_ext.endswith(ext):
            name_without_ext = name_without_ext[:-len(ext)]
            break

    # If the name ends with "burst" or similar, use it as the base
    # Output: basename_1.CR3, basename_2.CR3, etc.
    for i in range(n):
        output_name = f"{name_without_ext}_{i+1}.CR3"
        output_path = os.path.join(output_dir, output_name)
        print(f"  Extracting image {i+1}/{n} -> {output_name}...", end='', flush=True)
        size = cr3.extract_image(i, output_path)
        print(f" {size:,} bytes")

    if cleanup_temp:
        os.unlink(input_path)

    print(f"Done! Extracted {n} images.")


if __name__ == '__main__':
    main()
