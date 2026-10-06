# Meta Radar: rencana pengerjaan

Sumber: `meta-radar-dev-brief.md` (6 Okt 2026). Route `/radar`, API `/api/radar*`.

## Keputusan untuk poin "perlu dikonfirmasi ke owner" (default aman, semua bisa diubah lewat env)

| Pertanyaan | Default yang dipakai | Env |
|---|---|---|
| Label narasi | Kata kunci c-TF-IDF saja (`label_source = 'keywords'`). Kolom `llm_reviewed` disiapkan, belum ada generator LLM. | - |
| Ambang sample size | 20 / 50 sesuai brief | `RADAR_MIN_RESOLVED`, `RADAR_NORMAL_RESOLVED` |
| Rentang riwayat | Seluruh riwayat (`0` = semua) | `RADAR_HISTORY_DAYS` |
| Post X mingguan | Tidak dikerjakan | - |

## Fakta repo yang membentuk desain

- Embedding lore **tidak disimpan** di DB. `app/ml/features.py` menghitungnya sambil jalan (MiniLM, 384 dim, lazy load). Radar Worker menghitung ulang sekali sehari. Jika `sentence-transformers` tidak ada, worker **melewati run** (tidak ada cluster palsu dari embedding nol).
- Label ada di `tokens.status` (`passed` / `stalled` / `pending`); `resolved = passed|stalled`. Peak MC di `tokens.peak_mc`.
- HDBSCAN dan KMeans sudah ada di `scikit-learn>=1.4` (tanpa dependency baru). UMAP (`umap-learn`) opsional: bila tidak terpasang, proyeksi jatuh ke PCA 2D dengan tanda sumbu dikunci, dan `projection` dicatat di `params_json` serta ditampilkan di Methodology.
- Pola yang diikuti: `goforge_schema.py` (DDL idempotent), `goforge_worker.py` (lock `exclusive`, loop, flag enabled), `goforge_endpoints.py` (cache modul, serializer tunggal), `tests/test_*.py` (unittest, fungsi murni).

## Backend

1. `app/core/config.py`: setting `RADAR_*`.
2. `app/db/radar_schema.py` + mirror di `DDL.sql`: `radar_runs`, `radar_clusters` (+ `centroid_vec` untuk matching ID), `radar_points`.
3. `app/services/radar.py` (murni, tanpa I/O, semua diuji):
   - `wilson_interval`, `confidence_level`
   - `cluster_embeddings` (HDBSCAN, fallback KMeans/silhouette 8-25)
   - `ctfidf_keywords` (disaring profanity/URL/alamat)
   - `match_clusters` (Hungarian, cosine <= 0.25) + penetapan ID baru
   - `compute_cluster_metrics`, `narrative_status`, `build_run`
4. `app/services/radar_worker.py`: jalan sekali sehari (00:15 UTC), lock lintas instance, skip bila run hari ini sudah ada, simpan 3 tabel dalam satu transaksi.
5. `app/api/radar_endpoints.py`: `/api/radar`, `/api/radar/points`, `/api/radar/{id}`, `/api/radar/{id}/examples`; cache 1 jam. Aturan kejujuran ditegakkan **di API**: `n_resolved < 20` mengembalikan persentase `null`, points tanpa nama/alamat, contoh hanya resolved > 7 hari dan lolos `lore_withheld`.
6. `app/main.py`: daftarkan router dan loop worker.

## Frontend

`/radar` (layout + metadata), hook `useRadar`, `config/radarCopy.ts`, komponen di `components/radar/`: Hero, NarrativeMap (canvas, hover/klik), NarrativeTable (urut per kolom, kartu di mobile), NarrativeDrawer (Wilson bar, grafik 30 hari, peringatan saturation, contoh), ForCards, Methodology, Footer. Nav: tambah **Radar** setelah GoForge.

## Testing

- Unit (unittest): Wilson (nilai acuan), aturan sample size, stabilitas ID antar run dengan data tiruan, survival hanya dari resolved, lift terhadap baseline, saturation, status, c-TF-IDF, anonimitas points, filter contoh, tidak ada kata terlarang di copy frontend.
- API: FastAPI `TestClient` dengan loader di-mock.
- Frontend: `npm run lint`, `tsc --noEmit`, `next build`, lalu cek di browser (desktop + mobile, tanpa scroll horizontal) dengan backend tiruan.
- Tidak bisa diuji lokal: query SQL ke Postgres sungguhan (tidak ada DB lokal) dan embedding MiniLM sungguhan. Keduanya dicatat sebagai risiko.
