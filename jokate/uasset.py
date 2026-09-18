"""
.uasset / .umap 패키지 헤더 파서 (에디터 없이 읽기 전용).

FPackageFileSummary → 이름 테이블 → 임포트/익스포트 테이블 → 썸네일 테이블까지만 읽는다.
애셋 본문(export 직렬화 데이터)은 건드리지 않는다.

기준: UE 5.x 에디터 저장 패키지 (versioned). 쿠킹된 unversioned 패키지는 대상이 아님.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

PACKAGE_TAG = 0x9E2A83C1

# EUnrealEngineObjectUE4Version (필요한 것만)
VER_UE4_ADD_STRING_ASSET_REFERENCES_MAP = 384
VER_UE4_ENGINE_VERSION_OBJECT = 336
VER_UE4_PACKAGE_SUMMARY_HAS_COMPATIBLE_ENGINE_VERSION = 444
VER_UE4_SERIALIZE_TEXT_IN_PACKAGES = 459
VER_UE4_NAME_HASHES_SERIALIZED = 504
VER_UE4_PRELOAD_DEPENDENCIES_IN_PACKAGE = 507
VER_UE4_TemplateIndex_IN_COOKED_EXPORTS = 508
VER_UE4_ADDED_SEARCHABLE_NAMES = 510
VER_UE4_64BIT_EXPORTMAP_SERIALSIZES = 511
VER_UE4_ADDED_PACKAGE_SUMMARY_LOCALIZATION_ID = 516
VER_UE4_ADDED_PACKAGE_OWNER = 518
VER_UE4_NON_OUTER_PACKAGE_IMPORT = 520
VER_UE4_LOAD_FOR_EDITOR_GAME = 365
VER_UE4_COOKED_ASSETS_IN_EDITOR_SUPPORT = 485
VER_UE4_AUTOMATIC_VERSION = 522

# EUnrealEngineObjectUE5Version
UE5_NAMES_REFERENCED_FROM_EXPORT_DATA = 1001
UE5_PAYLOAD_TOC = 1002
UE5_OPTIONAL_RESOURCES = 1003
UE5_REMOVE_OBJECT_EXPORT_PACKAGE_GUID = 1005
UE5_TRACK_OBJECT_EXPORT_IS_INHERITED = 1006
UE5_ADD_SOFTOBJECTPATH_LIST = 1008
UE5_DATA_RESOURCES = 1009
UE5_SCRIPT_SERIALIZATION_OFFSET = 1010
UE5_METADATA_SERIALIZATION_OFFSET = 1014
UE5_VERSE_CELLS = 1015
UE5_PACKAGE_SAVED_HASH = 1016


class UAssetError(Exception):
    pass


class _Reader:
    def __init__(self, f: BinaryIO):
        self.f = f

    def tell(self) -> int:
        return self.f.tell()

    def seek(self, pos: int) -> None:
        self.f.seek(pos)

    def raw(self, n: int) -> bytes:
        b = self.f.read(n)
        if len(b) != n:
            raise UAssetError(f"unexpected EOF at {self.f.tell()} (wanted {n})")
        return b

    def i32(self) -> int:
        return struct.unpack("<i", self.raw(4))[0]

    def u32(self) -> int:
        return struct.unpack("<I", self.raw(4))[0]

    def i64(self) -> int:
        return struct.unpack("<q", self.raw(8))[0]

    def u16(self) -> int:
        return struct.unpack("<H", self.raw(2))[0]

    def boolean(self) -> bool:
        # UE는 bool을 uint32로 직렬화한다
        return self.u32() != 0

    def fstring(self) -> str:
        n = self.i32()
        if n == 0:
            return ""
        if n < 0:  # UTF-16LE, 널 포함
            data = self.raw(-n * 2)
            return data.decode("utf-16-le", errors="replace").rstrip("\x00")
        data = self.raw(n)
        return data.decode("utf-8", errors="replace").rstrip("\x00")


@dataclass
class EngineVersion:
    major: int
    minor: int
    patch: int
    changelist: int
    branch: str

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


@dataclass
class Import:
    class_package: str
    class_name: str
    outer_index: int
    object_name: str
    package_name: str = ""


@dataclass
class Export:
    class_index: int
    super_index: int
    template_index: int
    outer_index: int
    object_name: str
    object_flags: int
    serial_size: int
    serial_offset: int
    is_asset: bool


@dataclass
class Thumbnail:
    class_name: str
    object_path: str
    width: int
    height: int
    fmt: str  # "png" | "jpeg" | "raw"
    data: bytes


@dataclass
class Summary:
    legacy_version: int
    ver_ue4: int
    ver_ue5: int
    ver_licensee: int
    custom_versions: dict[str, int]
    total_header_size: int
    package_name: str
    package_flags: int
    name_count: int
    name_offset: int
    export_count: int
    export_offset: int
    import_count: int
    import_offset: int
    depends_offset: int
    soft_package_refs_count: int
    soft_package_refs_offset: int
    thumbnail_table_offset: int
    package_id: str  # Guid(hex) 또는 SavedHash(hex)
    saved_by: EngineVersion | None
    compatible_with: EngineVersion | None
    asset_registry_offset: int
    bulk_data_start: int


@dataclass
class Package:
    path: Path
    summary: Summary
    names: list[str]
    imports: list[Import]
    exports: list[Export]
    thumbnails: list[Thumbnail] = field(default_factory=list)

    # ---------- 조회 도우미 ----------
    def resolve(self, index: int) -> str:
        """FPackageIndex → 사람이 읽을 이름."""
        if index == 0:
            return ""
        if index < 0:
            return self.imports[-index - 1].object_name
        return self.exports[index - 1].object_name

    def import_full_path(self, i: int) -> str:
        """임포트의 Outer 체인을 따라 '/Game/Foo/Bar.Bar' 형태로 만든다."""
        imp = self.imports[i]
        if imp.class_name == "Package":
            return imp.object_name
        parts = [imp.object_name]
        outer = imp.outer_index
        while outer < 0:
            o = self.imports[-outer - 1]
            if o.class_name == "Package":
                return f"{o.object_name}.{'.'.join(reversed(parts))}"
            parts.append(o.object_name)
            outer = o.outer_index
        return ".".join(reversed(parts))

    def asset_export(self) -> Export | None:
        """패키지의 대표 애셋 export (IsAsset 플래그 우선, 없으면 Outer가 없는 첫 export)."""
        assets = [e for e in self.exports if e.is_asset]
        # Blueprint 패키지는 생성 클래스(_C)도 IsAsset일 수 있으니 원본 애셋을 우선
        for e in assets:
            if not self.resolve(e.class_index).endswith("GeneratedClass"):
                return e
        if assets:
            return assets[0]
        for e in self.exports:
            if e.outer_index == 0:
                return e
        return self.exports[0] if self.exports else None

    def asset_class(self) -> str:
        e = self.asset_export()
        if e is None:
            return ""
        return self.resolve(e.class_index)

    def asset_name(self) -> str:
        e = self.asset_export()
        return e.object_name if e else self.path.stem

    def hard_dependencies(self) -> list[str]:
        """임포트된 패키지 경로 목록 (/Script/ 제외 = 실제 애셋 의존성)."""
        out = []
        for imp in self.imports:
            if imp.class_name == "Package" and not imp.object_name.startswith("/Script/"):
                out.append(imp.object_name)
        return sorted(set(out))

    def script_dependencies(self) -> list[str]:
        return sorted({i.object_name for i in self.imports
                       if i.class_name == "Package" and i.object_name.startswith("/Script/")})

    def is_redirector(self) -> bool:
        return self.asset_class() == "ObjectRedirector"

    def blueprint_parent(self) -> str:
        """Blueprint 계열이면 부모 클래스 이름 추정 (BlueprintGeneratedClass export의 SuperIndex)."""
        for e in self.exports:
            cls = self.resolve(e.class_index)
            if cls.endswith("GeneratedClass") and e.super_index != 0:
                return self.resolve(e.super_index)
        return ""


# ---------------------------------------------------------------------------

def _read_engine_version(r: _Reader) -> EngineVersion:
    major, minor, patch = r.u16(), r.u16(), r.u16()
    changelist = r.u32()
    branch = r.fstring()
    return EngineVersion(major, minor, patch, changelist, branch)


def _read_summary(r: _Reader) -> Summary:
    tag = r.u32()
    if tag != PACKAGE_TAG:
        raise UAssetError(f"not a UE package (tag {tag:#x})")

    legacy = r.i32()
    if legacy >= 0:
        raise UAssetError(f"unsupported legacy version {legacy}")
    if legacy != -4:
        r.i32()  # LegacyUE3Version
    ver_ue4 = r.i32()
    ver_ue5 = r.i32() if legacy <= -8 else 0
    ver_licensee = r.i32()

    # 5.6+ (legacy -9): 버전 바로 뒤에 SavedHash(FIoHash, 20B)가 오고 Guid/Generations는 사라짐
    saved_hash = ""
    if legacy <= -9:
        saved_hash = r.raw(20).hex()

    def read_custom_versions() -> dict[str, int]:
        out: dict[str, int] = {}
        if legacy <= -2:
            for _ in range(r.i32()):
                guid = r.raw(16).hex()
                out[guid] = r.i32()
        return out

    # -9: SavedHash, TotalHeaderSize, CustomVersions / 그 이전: CustomVersions, TotalHeaderSize
    if legacy <= -9:
        total_header_size = r.i32()
        custom = read_custom_versions()
    else:
        custom = read_custom_versions()
        total_header_size = r.i32()

    if ver_ue4 == 0 and ver_ue5 == 0:
        raise UAssetError("unversioned (cooked) package is not supported")

    package_name = r.fstring()
    package_flags = r.u32()
    name_count, name_offset = r.i32(), r.i32()
    if ver_ue5 >= UE5_ADD_SOFTOBJECTPATH_LIST:
        r.i32(); r.i32()  # SoftObjectPathsCount / Offset
    if ver_ue4 >= VER_UE4_ADDED_PACKAGE_SUMMARY_LOCALIZATION_ID:
        r.fstring()  # LocalizationId
    if ver_ue4 >= VER_UE4_SERIALIZE_TEXT_IN_PACKAGES:
        r.i32(); r.i32()  # GatherableTextData Count / Offset
    export_count, export_offset = r.i32(), r.i32()
    import_count, import_offset = r.i32(), r.i32()
    if ver_ue5 >= UE5_VERSE_CELLS:
        r.i32(); r.i32(); r.i32(); r.i32()  # CellExport/CellImport Count+Offset
    if ver_ue5 >= UE5_METADATA_SERIALIZATION_OFFSET:
        r.i32()  # MetaDataOffset
    depends_offset = r.i32()
    soft_count = soft_offset = 0
    if ver_ue4 >= VER_UE4_ADD_STRING_ASSET_REFERENCES_MAP:
        soft_count, soft_offset = r.i32(), r.i32()
    if ver_ue4 >= VER_UE4_ADDED_SEARCHABLE_NAMES:
        r.i32()  # SearchableNamesOffset
    thumbnail_table_offset = r.i32()

    if legacy <= -9:
        package_id = saved_hash
        if ver_ue5 >= 1018:
            r.raw(8)  # 1018(5.7)부터 추가된 8바이트 (관측값 0, 용도 미확인)
        r.raw(16)  # PersistentGuid
    else:
        package_id = r.raw(16).hex()  # FGuid
        filter_editor_only = bool(package_flags & 0x80000000)
        if ver_ue4 >= VER_UE4_ADDED_PACKAGE_OWNER and not filter_editor_only:
            r.raw(16)  # PersistentGuid
            if ver_ue4 < VER_UE4_NON_OUTER_PACKAGE_IMPORT:
                r.raw(16)  # OwnerPersistentGuid
    # Generations
    for _ in range(r.i32()):
        r.i32(); r.i32()

    saved_by = compatible = None
    if ver_ue4 >= VER_UE4_ENGINE_VERSION_OBJECT:
        saved_by = _read_engine_version(r)
    if ver_ue4 >= VER_UE4_PACKAGE_SUMMARY_HAS_COMPATIBLE_ENGINE_VERSION:
        compatible = _read_engine_version(r)

    r.u32()  # CompressionFlags
    if r.i32() != 0:
        raise UAssetError("compressed chunks are not supported")
    r.u32()  # PackageSource
    n = r.i32()  # AdditionalPackagesToCook
    for _ in range(n):
        r.fstring()
    if legacy > -7:
        r.i32()  # NumTextureAllocations
    asset_registry_offset = r.i32()
    bulk_data_start = r.i64()

    return Summary(
        legacy_version=legacy, ver_ue4=ver_ue4, ver_ue5=ver_ue5, ver_licensee=ver_licensee,
        custom_versions=custom, total_header_size=total_header_size, package_name=package_name,
        package_flags=package_flags, name_count=name_count, name_offset=name_offset,
        export_count=export_count, export_offset=export_offset,
        import_count=import_count, import_offset=import_offset, depends_offset=depends_offset,
        soft_package_refs_count=soft_count, soft_package_refs_offset=soft_offset,
        thumbnail_table_offset=thumbnail_table_offset, package_id=package_id,
        saved_by=saved_by, compatible_with=compatible,
        asset_registry_offset=asset_registry_offset, bulk_data_start=bulk_data_start,
    )


def _read_names(r: _Reader, s: Summary) -> list[str]:
    r.seek(s.name_offset)
    names = []
    for _ in range(s.name_count):
        names.append(r.fstring())
        if s.ver_ue4 >= VER_UE4_NAME_HASHES_SERIALIZED:
            r.u16(); r.u16()
    return names


def _fname(r: _Reader, names: list[str]) -> str:
    idx, num = r.i32(), r.i32()
    base = names[idx] if 0 <= idx < len(names) else f"<bad:{idx}>"
    return f"{base}_{num - 1}" if num > 0 else base


def _read_imports(r: _Reader, s: Summary, names: list[str]) -> list[Import]:
    r.seek(s.import_offset)
    out = []
    for _ in range(s.import_count):
        class_package = _fname(r, names)
        class_name = _fname(r, names)
        outer = r.i32()
        obj = _fname(r, names)
        pkg = ""
        if s.ver_ue4 >= VER_UE4_NON_OUTER_PACKAGE_IMPORT:
            pkg = _fname(r, names)
        if s.ver_ue5 >= UE5_OPTIONAL_RESOURCES:
            r.boolean()  # bImportOptional
        out.append(Import(class_package, class_name, outer, obj, pkg))
    return out


def _read_exports(r: _Reader, s: Summary, names: list[str]) -> list[Export]:
    r.seek(s.export_offset)
    out = []
    for _ in range(s.export_count):
        class_index = r.i32()
        super_index = r.i32()
        template_index = r.i32() if s.ver_ue4 >= VER_UE4_TemplateIndex_IN_COOKED_EXPORTS else 0
        outer_index = r.i32()
        obj = _fname(r, names)
        flags = r.u32()
        serial_size = r.i64() if s.ver_ue4 >= VER_UE4_64BIT_EXPORTMAP_SERIALSIZES else r.i32()
        serial_offset = r.i64() if s.ver_ue4 >= VER_UE4_64BIT_EXPORTMAP_SERIALSIZES else r.i32()
        r.boolean()  # bForcedExport
        r.boolean()  # bNotForClient
        r.boolean()  # bNotForServer
        if s.ver_ue5 < UE5_REMOVE_OBJECT_EXPORT_PACKAGE_GUID:
            r.raw(16)  # PackageGuid
        if s.ver_ue5 >= UE5_TRACK_OBJECT_EXPORT_IS_INHERITED:
            r.boolean()  # bIsInheritedInstance
        r.u32()  # PackageFlags
        if s.ver_ue4 >= VER_UE4_LOAD_FOR_EDITOR_GAME:
            r.boolean()  # bNotAlwaysLoadedForEditorGame
        is_asset = False
        if s.ver_ue4 >= VER_UE4_COOKED_ASSETS_IN_EDITOR_SUPPORT:
            is_asset = r.boolean()
        if s.ver_ue5 >= UE5_OPTIONAL_RESOURCES:
            r.boolean()  # bGeneratePublicHash
        if s.ver_ue4 >= VER_UE4_PRELOAD_DEPENDENCIES_IN_PACKAGE:
            for _ in range(5):
                r.i32()
        if s.ver_ue5 >= UE5_SCRIPT_SERIALIZATION_OFFSET:
            r.i64(); r.i64()  # ScriptSerializationStart/End
        out.append(Export(class_index, super_index, template_index, outer_index, obj,
                          flags, serial_size, serial_offset, is_asset))
    return out


def _read_thumbnails(r: _Reader, s: Summary) -> list[Thumbnail]:
    if s.thumbnail_table_offset <= 0:
        return []
    r.seek(s.thumbnail_table_offset)
    n = r.i32()
    entries = []
    for _ in range(n):
        cls = r.fstring()
        path = r.fstring()
        off = r.i32()
        entries.append((cls, path, off))
    out = []
    for cls, path, off in entries:
        r.seek(off)
        w = r.i32()
        h = r.i32()
        size = r.i32()
        data = r.raw(size) if size > 0 else b""
        if not data:
            continue  # DataTable 등 썸네일 없는 항목
        if data.startswith(b"\x89PNG"):
            fmt = "png"
        elif data.startswith(b"\xff\xd8"):
            fmt = "jpeg"
        else:
            fmt = "raw"
        out.append(Thumbnail(cls, path, abs(w), abs(h), fmt, data))  # 음수 = JPEG 표시
    return out


def read_package(path: str | Path, *, thumbnails: bool = False) -> Package:
    path = Path(path)
    with open(path, "rb") as f:
        r = _Reader(f)
        s = _read_summary(r)
        names = _read_names(r, s)
        imports = _read_imports(r, s, names)
        exports = _read_exports(r, s, names)
        pkg = Package(path, s, names, imports, exports)
        if thumbnails:
            try:
                pkg.thumbnails = _read_thumbnails(r, s)
            except (UAssetError, struct.error):
                pkg.thumbnails = []
        return pkg


if __name__ == "__main__":
    import sys
    p = read_package(sys.argv[1], thumbnails=True)
    s = p.summary
    print(f"{p.path.name}")
    print(f"  legacy={s.legacy_version} ue4={s.ver_ue4} ue5={s.ver_ue5} saved_by={s.saved_by}")
    print(f"  package={s.package_name} flags={s.package_flags:#x} header={s.total_header_size}")
    print(f"  names={s.name_count} imports={s.import_count} exports={s.export_count}")
    print(f"  asset={p.asset_name()} class={p.asset_class()} parent={p.blueprint_parent()!r}")
    print(f"  deps={p.hard_dependencies()}")
    print(f"  thumbs={[(t.class_name, t.width, t.height, t.fmt, len(t.data)) for t in p.thumbnails]}")
