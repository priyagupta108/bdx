import os
from datetime import datetime, timedelta
from pathlib import Path
from random import shuffle
from typing import Optional

from bdx.binary import BinaryDirectory, NameDemangler


def create_fake_elf_file(path: Path, mtime: Optional[datetime] = None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x7fELF")
    if mtime is not None:
        secs = (mtime - datetime.fromtimestamp(0)).seconds
        os.utime(path, (secs, secs))


def mtime(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime)


def setup_tmp_dir(
    file_list: list[Path],
    files_to_delete: Optional[list[Path]] = None,
    files_to_modify: Optional[list[Path]] = None,
) -> tuple[list[Path], list[Path], list[Path], datetime]:
    if files_to_delete is None:
        files_to_delete = []
    if files_to_modify is None:
        files_to_modify = []

    max_mtime = datetime.now() - timedelta(seconds=1)
    for f in file_list:
        create_fake_elf_file(f, max_mtime)
    for f in files_to_delete:
        f.unlink()
    for f in files_to_modify:
        f.touch()
    return file_list, files_to_delete, files_to_modify, max_mtime


def shuffled_int_range(stop):
    l = list(range(stop))
    shuffle(l)
    return l


def test_name_demangler():
    with NameDemangler() as nd:
        nd.demangle_async("memset")
        nd.demangle_async("_Z12cxx_functionSt6vectorIiSaIiEE")
        nd.demangle_async(".text._Z12cxx_functionSt6vectorIiSaIiEE")

        assert (
            nd.get_demangled_name("_Z12cxx_functionSt6vectorIiSaIiEE")
            == "cxx_function(std::vector<int, std::allocator<int> >)"
        )

        assert (
            nd.get_demangled_name(".text._Z12cxx_functionSt6vectorIiSaIiEE")
            == ".text._Z12cxx_functionSt6vectorIiSaIiEE"
        )

        assert nd.get_demangled_name("memset") == "memset"

        assert nd.get_demangled_name("Unknown") == None


def test_name_demangler_many_at_once():
    with NameDemangler() as nd:
        fmt = "_Z12cxx_func_{i:0>3}v"
        demangled_fmt = "cxx_func_{i:0>3}()"
        results = {}

        for j, i in enumerate(shuffled_int_range(500)):
            mangled = fmt.format(i=i)
            nd.demangle_async(mangled)

            # Trigger generating new process every N elements
            if j % 32 == 0:
                assert nd.get_demangled_name(mangled) is not None

        for i in shuffled_int_range(500):
            mangled = fmt.format(i=i)
            demangled = nd.get_demangled_name(mangled)

            results[i] = demangled

        for i in range(500):
            demangled = demangled_fmt.format(i=i)

            assert results[i] == demangled


def test_find_files(tmp_path):
    create_fake_elf_file(tmp_path / "0.o")
    create_fake_elf_file(tmp_path / "1.o")
    create_fake_elf_file(tmp_path / "subdir" / "subdir" / "2.o")
    create_fake_elf_file(tmp_path / "subdir" / "subdir" / "3.txt")
    create_fake_elf_file(tmp_path / "subdir" / "4.txt")
    (tmp_path / "subdir" / "5.o").touch()

    bdir = BinaryDirectory(tmp_path)
    deleted_files = list(bdir.deleted_files())
    changed_files = list(bdir.changed_files())

    assert deleted_files == []
    assert set(changed_files) == set(
        [
            tmp_path / "0.o",
            tmp_path / "1.o",
            tmp_path / "subdir" / "subdir" / "2.o",
        ]
    )


def test_find_changed_files(tmp_path):
    file_list, _, files_to_modify, max_mtime = setup_tmp_dir(
        file_list=[
            tmp_path / "0.o",
            tmp_path / "1.o",
            tmp_path / "2.o",
            tmp_path / "3.o",
            tmp_path / "4.o",
            tmp_path / "5.o",
            tmp_path / "subdir" / "6.o",
            tmp_path / "subdir2" / "subdir3" / "7.o",
            tmp_path / "subdir2" / "subdir3" / "8.o",
            tmp_path / "subdir2" / "9.o",
            tmp_path / "subdir2" / "subdir4" / "10.o",
        ],
        files_to_delete=[],
        files_to_modify=[
            tmp_path / "1.o",
            tmp_path / "subdir" / "6.o",
        ],
    )

    bdir = BinaryDirectory(
        tmp_path,
        last_mtime=max_mtime,
        previous_file_list=file_list,
    )
    deleted_files = list(bdir.deleted_files())
    changed_files = list(bdir.changed_files())

    assert set(deleted_files) == set()
    assert set(changed_files) == set(files_to_modify)


def test_find_deleted_files(tmp_path):
    file_list, files_to_delete, files_to_modify, max_mtime = setup_tmp_dir(
        file_list=[
            tmp_path / "0.o",
            tmp_path / "1.o",
            tmp_path / "2.o",
            tmp_path / "3.o",
            tmp_path / "4.o",
            tmp_path / "5.o",
            tmp_path / "subdir" / "6.o",
            tmp_path / "subdir2" / "subdir3" / "7.o",
            tmp_path / "subdir2" / "subdir3" / "8.o",
            tmp_path / "subdir2" / "9.o",
            tmp_path / "subdir2" / "subdir4" / "10.o",
        ],
        files_to_delete=[
            tmp_path / "3.o",
            tmp_path / "subdir2" / "subdir3" / "8.o",
            tmp_path / "subdir2" / "subdir4" / "10.o",
        ],
        files_to_modify=[
            tmp_path / "0.o",
            tmp_path / "subdir" / "6.o",
        ],
    )

    bdir = BinaryDirectory(
        tmp_path,
        last_mtime=max_mtime,
        previous_file_list=file_list,
    )
    changed_files = list(bdir.changed_files())
    deleted_files = list(bdir.deleted_files())

    assert set(changed_files) == set(files_to_modify)
    assert set(deleted_files) == set(files_to_delete)
