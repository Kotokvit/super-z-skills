#!/usr/bin/env python3
"""
weight_address_index.py
=======================
Path D, Шаг 1: Обратный индекс meaning-vector → tensor_address.

Концепция:
  Классическая LLM:     weights → active forward pass
  Инвертированная LLM:  meaning-query → address resolver → sparse weights load

Этот скрипт строит "weight database" — индекс, который по meaning-координатам
(archetype_id + ε + name-features) возвращает физический адрес тензора в
model.safetensors (byte offset, length, dtype, shape).

Вход:
  - work/analysis/poler_eri_out/archetypes.json (10 архетипов + члены)
  - work/analysis/poler_eri_out/sparse_graph_edges.txt (3019 рёбер)
  - work/analysis/toolkit_out/clusters.json (611 тензоров с cluster id)
  - work/analysis/tensors_stats.json (per-tensor stats)
  - work/qwen-asr/model.safetensors (физические веса)
  - work/qwen-asr/model.safetensors.index.json (если есть — мультифайл)

Выход:
  - work/analysis/weight_db/
      ├── index.json         — главный обратный индекс
      ├── address_table.json — физические адреса тензоров
      └── manifest.json      — метаданные индекса
"""

import json
import os
import re
import struct
import hashlib
from collections import defaultdict
from pathlib import Path

# ============ КОНСТАНТЫ ============
BASE = Path("/home/z/my-project")
WORK = BASE / "work"
ANALYSIS = WORK / "analysis"
ERI_OUT = ANALYSIS / "poler_eri_out"
TOOLKIT_OUT = ANALYSIS / "toolkit_out"
WEIGHTS_DIR = WORK / "qwen-asr"
OUT_DIR = ANALYSIS / "weight_db"
OUT_DIR.mkdir(parents=True, exist_ok=True)


# ============ ШАГ 1: ПАРСИНГ SAFETENSORS HEADER ============
def parse_safetensors_header(safetensors_path: Path) -> dict:
    """
    Safetensors формат: <header_len u64><header_json><raw_bytes>
    Возвращает dict {tensor_name: {dtype, shape, data_offsets: [start, end]}}
    """
    with open(safetensors_path, "rb") as f:
        header_len_bytes = f.read(8)
        header_len = struct.unpack("<Q", header_len_bytes)[0]
        header_json = f.read(header_len).decode("utf-8")
        header = json.loads(header_json)

    # Убираем служебный ключ __metadata__
    tensors_meta = {k: v for k, v in header.items() if k != "__metadata__"}
    return tensors_meta, header.get("__metadata__", {})


# ============ ШАГ 2: ИЗВЛЕЧЕНИЕ NAME FEATURES (токенизация без токенизатора) ============
NAME_PATTERN = re.compile(r"[._\-/\s]+")

def extract_name_features(tensor_name: str) -> dict:
    """
    Разбирает имя тензора на атомы + биграммы БЕЗ BPE/SentencePiece.
    Это и есть "инвертированный токенизатор" — name → meaning-atoms.
    """
    atoms = [a for a in NAME_PATTERN.split(tensor_name.lower()) if a and len(a) > 1]
    # биграммы для повышения resolution
    bigrams = [f"{atoms[i]}_{atoms[i+1]}" for i in range(len(atoms) - 1)] if len(atoms) >= 2 else []
    # "роль" тензора — последняя атом-компонента (bias / weight / norm)
    role = atoms[-1] if atoms else "unknown"
    # структурный путь — всё кроме последнего
    path = atoms[:-1] if len(atoms) > 1 else []
    return {
        "atoms": atoms,
        "bigrams": bigrams,
        "role": role,
        "path": path,
        "atom_count": len(atoms),
    }


# ============ ШАГ 3: СБОРКА ADDRESS TABLE ============
def build_address_table(tensors_meta: dict, safetensors_path: Path) -> dict:
    """
    Для каждого тензора: имя → {byte_offset, byte_length, dtype, shape, file}
    Это и есть физическая "БД весов".
    """
    address_table = {}
    safetensors_size = safetensors_path.stat().st_size
    # header overhead: 8 bytes length + header JSON
    with open(safetensors_path, "rb") as f:
        header_len = struct.unpack("<Q", f.read(8))[0]
    header_overhead = 8 + header_len

    for name, meta in tensors_meta.items():
        start, end = meta["data_offsets"]
        address_table[name] = {
            "file": safetensors_path.name,
            "file_path": str(safetensors_path),
            "byte_offset": header_overhead + start,
            "byte_length": end - start,
            "dtype": meta["dtype"],
            "shape": meta["shape"],
            "numel": 1,
            "file_size": safetensors_size,
        }
        # посчитаем numel
        n = 1
        for d in meta["shape"]:
            n *= d
        address_table[name]["numel"] = n

    return address_table, header_overhead


# ============ ШАГ 4: СБОРКА MEANING INDEX ============
def build_meaning_index(
    archetypes: list,
    clusters: dict,
    edges: list,
    tensors_stats: dict,
) -> dict:
    """
    Для каждого тензора: имя → meaning-coordinates {archetype_id, archetype_name, cluster_id, epsilon, role, neighbors, tags}
    Использует clusters.archetypes (полный маппинг 611 тензоров) + poler_eri.archetypes (топовые).
    """
    # 1. Полный маппинг из toolkit/clusters.archetypes (ключ: имя тензора)
    toolkit_archetypes = clusters.get("archetypes", {}) if clusters else {}
    # toolkit_archetypes[name] = {cluster, archetype, tags}

    # 2. Карта cluster_id → имя архетипа из poler_eri (топовые имена)
    cluster_id_to_name = {}
    for arch in archetypes:
        cluster_id_to_name[arch["id"]] = arch["archetype_name"]

    # 3. карта name → neighbors (из sparse graph)
    name_to_neighbors = defaultdict(list)
    for src, dst, weight in edges:
        name_to_neighbors[src].append({"neighbor": dst, "weight": float(weight)})

    # 4. сборка финального индекса
    meaning_index = {}
    for name, stats in tensors_stats.items():
        features = extract_name_features(name)
        toolkit_info = toolkit_archetypes.get(name, {})
        arch_id = int(toolkit_info.get("cluster", -1))
        arch_name_toolkit = toolkit_info.get("archetype", "UNKNOWN")
        arch_name_poler = cluster_id_to_name.get(arch_id, arch_name_toolkit)
        meaning_index[name] = {
            "archetype_id": arch_id,
            "archetype_name": arch_name_poler,
            "archetype_name_toolkit": arch_name_toolkit,
            "tags": toolkit_info.get("tags", []),
            "epsilon": stats.get("epsilon", 0.0),
            "mean": stats.get("mean", 0.0),
            "std": stats.get("std", 0.0),
            "sparsity": stats.get("sparsity", 0.0),
            "role": features["role"],
            "path_atoms": features["path"],
            "atoms": features["atoms"],
            "bigrams": features["bigrams"],
            "neighbors": name_to_neighbors.get(name, []),
        }

    return meaning_index


# ============ ШАГ 5: СБОРКА ОБРАТНОГО ИНДЕКСА ============
def build_reverse_index(meaning_index: dict) -> dict:
    """
    Строит несколько обратных индексов для быстрого AddressResolver:
      - by_archetype: archetype_id → [tensor_names]
      - by_role: role → [tensor_names]
      - by_atom: atom → [tensor_names]  (TF-IDF позже)
      - by_bigram: bigram → [tensor_names]
    """
    by_archetype = defaultdict(list)
    by_role = defaultdict(list)
    by_atom = defaultdict(list)
    by_bigram = defaultdict(list)

    for name, meta in meaning_index.items():
        if meta["archetype_id"] >= 0:
            by_archetype[meta["archetype_id"]].append(name)
        by_role[meta["role"]].append(name)
        for atom in meta["atoms"]:
            by_atom[atom].append(name)
        for bigram in meta["bigrams"]:
            by_bigram[bigram].append(name)

    return {
        "by_archetype": dict(by_archetype),
        "by_role": dict(by_role),
        "by_atom": dict(by_atom),
        "by_bigram": dict(by_bigram),
    }


# ============ ГЛАВНЫЙ ПАЙПЛАЙН ============
def main():
    print("=" * 70)
    print("  WEIGHT ADDRESS INDEX — Path D, Шаг 1")
    print("=" * 70)

    # 1. Парсинг safetensors
    print("\n[1/5] Парсинг safetensors header...")
    safetensors_path = WEIGHTS_DIR / "model.safetensors"
    if not safetensors_path.exists():
        raise FileNotFoundError(f"Safetensors не найден: {safetensors_path}")
    tensors_meta, st_metadata = parse_safetensors_header(safetensors_path)
    print(f"  Найдено тензоров: {len(tensors_meta)}")
    print(f"  Метаданные safetensors: {st_metadata}")

    # 2. Address table
    print("\n[2/5] Сборка физической address table...")
    address_table, header_overhead = build_address_table(tensors_meta, safetensors_path)
    print(f"  Адресов: {len(address_table)}")
    print(f"  Header overhead: {header_overhead} bytes")
    # Размер выборки
    sample_name = list(address_table.keys())[0]
    print(f"  Пример: {sample_name}")
    print(f"    {address_table[sample_name]}")

    # 3. Загрузка POLER-ERI артефактов
    print("\n[3/5] Загрузка архетипов и кластеров...")
    with open(ERI_OUT / "archetypes.json") as f:
        arch_data = json.load(f)
    archetypes = arch_data["archetypes"]
    print(f"  Архетипов: {len(archetypes)}")

    with open(TOOLKIT_OUT / "clusters.json") as f:
        clusters = json.load(f)
    # clusters.json хранит {names: [...], labels: [...], archetypes: [...]}
    # Конвертируем в удобный формат
    cluster_names = clusters.get("names", [])
    cluster_labels = clusters.get("labels", [])
    print(f"  Names: {len(cluster_names)}, Labels: {len(cluster_labels)}")

    # 4. Загрузка рёбер
    print("\n[4/5] Загрузка разреженного графа...")
    edges = []
    with open(ERI_OUT / "sparse_graph_edges.txt") as f:
        for line in f:
            parts = line.strip().split("\t")
            if len(parts) == 3:
                edges.append((parts[0], parts[1], parts[2]))
    print(f"  Рёбер: {len(edges)}")

    # 5. Загрузка per-tensor stats
    with open(ANALYSIS / "tensors_stats.json") as f:
        tensors_stats = json.load(f)
    print(f"  Stats записей: {len(tensors_stats)}")

    # Проверим покрытие архетипами
    arch_count = sum(1 for n in tensors_stats if n in (clusters.get('archetypes', {}) if clusters else {}))
    print(f"  Покрытие архетипами: {arch_count}/{len(tensors_stats)}")

    # 6. Сборка meaning index
    print("\n[5/5] Сборка meaning index + обратного индекса...")
    meaning_index = build_meaning_index(archetypes, clusters, edges, tensors_stats)
    print(f"  Записей в meaning index: {len(meaning_index)}")

    reverse_index = build_reverse_index(meaning_index)
    print(f"  Обратных индексов: archetype={len(reverse_index['by_archetype'])}, "
          f"role={len(reverse_index['by_role'])}, "
          f"atom={len(reverse_index['by_atom'])}, "
          f"bigram={len(reverse_index['by_bigram'])}")

    # 7. Сохранение
    print("\n[Сохранение]")

    # address_table — компактная версия
    address_table_path = OUT_DIR / "address_table.json"
    with open(address_table_path, "w") as f:
        json.dump(address_table, f, indent=1)
    print(f"  {address_table_path} ({address_table_path.stat().st_size // 1024} KB)")

    # meaning_index
    meaning_index_path = OUT_DIR / "meaning_index.json"
    with open(meaning_index_path, "w") as f:
        json.dump(meaning_index, f, indent=1)
    print(f"  {meaning_index_path} ({meaning_index_path.stat().st_size // 1024} KB)")

    # reverse_index
    reverse_index_path = OUT_DIR / "reverse_index.json"
    with open(reverse_index_path, "w") as f:
        json.dump(reverse_index, f, indent=1)
    print(f"  {reverse_index_path} ({reverse_index_path.stat().st_size // 1024} KB)")

    # manifest
    manifest = {
        "model": "Qwen/Qwen3-ASR-0.6B-hf",
        "safetensors_file": str(safetensors_path),
        "safetensors_size_bytes": safetensors_path.stat().st_size,
        "safetensors_sha8": hashlib.sha256(safetensors_path.read_bytes()).hexdigest()[:8],
        "tensor_count": len(tensors_meta),
        "archetype_count": len(archetypes),
        "edge_count": len(edges),
        "header_overhead_bytes": header_overhead,
        "created_by": "weight_address_index.py",
        "components": {
            "address_table": str(address_table_path),
            "meaning_index": str(meaning_index_path),
            "reverse_index": str(reverse_index_path),
        },
        "archetypes": [
            {"id": a["id"], "name": a["archetype_name"], "size": a["size"],
             "eps_mean": a["eps_mean"], "self_cosine": a["self_cosine"]}
            for a in archetypes
        ],
    }
    manifest_path = OUT_DIR / "manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"  {manifest_path} ({manifest_path.stat().st_size} bytes)")

    # Итоговая статистика
    print("\n" + "=" * 70)
    print("  ИТОГ")
    print("=" * 70)
    print(f"  Weight DB готов: {OUT_DIR}")
    print(f"  Тензоров адресуемо: {len(address_table)}")
    print(f"  Meaning-координат: {len(meaning_index)}")
    print(f"  Обратных индексов: {len(reverse_index)}")
    print(f"  Размер БД: {sum(p.stat().st_size for p in OUT_DIR.glob('*.json')) // 1024} KB")
    print(f"  Сжатие: {safetensors_path.stat().st_size:,} bytes весов → "
          f"{sum(p.stat().st_size for p in OUT_DIR.glob('*.json')):,} bytes индекса")
    print()
    print("  Архетипы (memory regions):")
    for a in archetypes:
        print(f"    [{a['id']:2d}] {a['archetype_name']:30s} "
              f"size={a['size']:4d}  ε={a['eps_mean']:.2f}  self_cos={a['self_cosine']:.6f}")


if __name__ == "__main__":
    main()
