# Implementation Plan: The Desk (Live Trading Page)

Oct 3, 2026 · berdasarkan *Developer Brief: The Desk* (@ronald)

Dokumen ini memetakan brief ke codebase saat ini: apa yang sudah ada, apa yang belum ada (dan ternyata lebih besar dari yang diasumsikan brief), lalu urutan kerja per fase lengkap dengan file yang disentuh.

---

## 1. Temuan audit (brief vs. kode sekarang)

Brief menyebut "dua endpoint baru, empat event WS, satu worker auto-post, sisanya memakai sistem yang sudah ada". Setelah dicek, sebagian besar fondasi trading **belum ada**. Halaman Desk bisa dibangun, tapi panel Waiting / Open akan kosong selamanya sampai komponen di bawah ini dibuat.

### Sudah ada dan bisa dipakai ulang

| Komponen | Lokasi | Dipakai untuk |
| --- | --- | --- |
| Gate trading (`trade_gate`, `golem_paused`, `record_trade_decision`) | `backend/app/services/golem_guard.py` | State Gated / Paused, `blocked_by` |
| Indexer swap Golem (`golem_swaps`) + decode buy/sell dari receipt | `epoch_watcher.py`, `trade_journal.decode_swap` | Harga entry/exit dan ukuran dari onchain |
| PnL average-cost (`compute_results`) | `backend/app/services/trade_journal.py` | PnL Closed trades |
| `epc_burns` + total burn | `epochs_schema.py`, `/api/epochs` | Status bar "EPC burned" |
| WS `ConnectionManager.broadcast` (format berbasis key) | `backend/app/api/websocket.py` | Event `desk_*` |
| `TwitterService.post_tweet` (OAuth1, cooldown, log `twitter_posts`) | `backend/app/services/twitter_service.py` | Worker auto-post |
| Model run terbaru + `blocked_by` | `endpoints.get_latest_model`, `model_runs` | Model run ID, proven floor, gate |
| `/api/trades` + `TradeJournal.tsx` | `trades_endpoints.py`, `components/epochs/` | Referensi pola & komponen |
| Token tokens (peak_mc, holders, launched_at) | `models.Token` | Kolom Watching |

### Gap / blocker

| # | Temuan | Lokasi | Dampak ke brief |
| --- | --- | --- | --- |
| G1 | **Tidak ada trade executor.** Tidak ada kode yang memilih kandidat, mengirim swap, atau menjalankan exit rule. `record_trade_decision` disiapkan tapi belum ada pemanggilnya. | `backend/app/services/` | State Waiting / Entering / In position, panel Waiting, Open positions, exit rule: semua butuh executor. |
| G2 | **Model tidak dipersist.** `train_model_and_evaluate` hanya mengembalikan metrik; `clf` dibuang. | `backend/app/ml/trainer.py`, `model_worker.py` | Survival probability live di Watching tidak bisa dihitung. |
| G3 | **Tidak ada scoring live untuk token yang sedang dipantau.** Ingest worker hanya broadcast `{"token": ...}` tanpa skor. | `ingest_worker.py` | Kolom survival + status "scoring / below threshold". |
| G4 | **Top signals per keputusan tidak ada.** Yang ada hanya `feature_importance` global; `golem_trade_decisions.top_signal` cuma satu string. | `trainer.py`, `epochs_schema.py` | Kartu Why butuh 3 kontribusi terbesar per token (SHAP / `pred_contrib` LightGBM). |
| G5 | `golem_trade_decisions` belum menyimpan `why` lengkap, hash, threshold, exit plan, size rule. | `epochs_schema.py` | Immutability + hash `why` (acceptance). |
| G6 | Tidak ada state machine Golem (6 state) dan heartbeat `last_decision_at`. | — | Status bar. |
| G7 | Tidak ada pembacaan saldo wallet (`eth_getBalance`) dan harga mark live dari pair (`getReserves`). | `chain_reader.py` | Saldo, PnL unrealized. |
| G8 | Tidak ada private mempool / MEV-protected RPC yang terkonfirmasi untuk Robinhood Chain (4663). | — | Front-running protection (aturan teknis 3). |
| G9 | Private key signer belum ada di backend (dan memang tidak boleh sembarangan). Perlu keputusan wallet vs agent (Open question #7). | `config.py` | Executor tidak bisa jalan. |
| G10 | `/stream` broadcast ke semua klien tanpa filter: apa pun yang di-broadcast bisa dibaca publik. | `websocket.py` | Event anonim harus disusun di server, bukan difilter di klien. |

**Rekomendasi pemecahan scope:** pisahkan menjadi dua deliverable.

- **Desk v1 (bisa jalan sekarang, mode Gated):** halaman, status bar, Watching dengan skor live, Closed/Open dari data onchain yang ada, API + WS + test anonimisasi. Ini memenuhi semua acceptance criteria yang tidak bergantung pada trade nyata.
- **Golem Executor (butuh keputusan Open questions dulu):** pipeline kandidat → entry → exit, signer, MEV protection, X auto-post. Ini sebenarnya inti trading epoch III dan layak jadi brief/ticket sendiri.

---

## 2. Keputusan yang dibutuhkan (owner: tim / @ronald)

Dari Open questions di brief, ditambah dua yang muncul dari audit. Fase 1–3 bisa jalan dengan default; Fase 4 (executor) tidak boleh live sebelum K1–K8 dijawab.

- [ ] **K1. Threshold entry** — tetap (mis. 0.65) atau `max(0.65, f(proven_floor))`? *Default sementara: konstanta `DESK_ENTRY_THRESHOLD` di config.*
- [ ] **K2. Ukuran posisi** — ETH per trade dan max posisi terbuka. *Saran: 0.05 ETH, max 3 posisi, tidak pernah > 20% saldo.*
- [ ] **K3. Exit** — TP / SL / max hold. *Saran mengikuti `onchain_dataset.py`: TP MC $30K, SL MC $5K, max 48 jam — sudah ada dataset untuk backtest.*
- [ ] **K4. Filter likuiditas** minimum (kasus BlastBack). *Saran: liq ≥ $5K dan slippage simulasi < 5%.*
- [ ] **K5. Delay post X** — saran 10 menit.
- [ ] **K6. Aturan burn** — per trade atau net per periode, high-water mark? Menentukan kolom "EPC burned" per trade.
- [ ] **K7. Wallet vs agent key** — siapa yang menandatangani swap? Di mana private key disimpan (env Railway? KMS?).
- [ ] **K8. Daftar token dikecualikan** — EPC, "Eth And Dream", dan alamat lain milik tim.
- [ ] **K9 (baru). MEV protection** — apakah Robinhood Chain punya private RPC/sequencer-direct? Kalau tidak ada, terima risiko dan andalkan anonimisasi + slippage limit.
- [ ] **K10 (baru). Definisi "Watching" vs feed publik** — Watching memakai token dari ingest feed yang sudah ada (≥ $10K). Konfirmasi bahwa menampilkan skor survival untuk semua token itu aman (skor sendiri tidak membocorkan kandidat selama tidak ada label "queued").

---

## 3. Fase 0: Prasyarat model (scoring live)

| Task | File | Detail |
| --- | --- | --- |
| 0.1 | `backend/app/ml/trainer.py` | Kembalikan juga `clf` + daftar kolom fitur. |
| 0.2 | `backend/app/services/model_worker.py` | Simpan model (`booster.model_to_string()`) ke tabel `model_artifacts (run_id PK, model_text, sha256)` agar tetap ada setelah redeploy Railway. Simpan sha256 untuk audit. |
| 0.3 | `backend/app/services/scorer.py` (baru) | `load_live_model(db)` (cache per `run_id`), `score(token_row) -> float`, `top_signals(token_row, k=3)` memakai `predict(..., pred_contrib=True)` LightGBM → nama fitur, nilai mentah yang bisa dibaca manusia ("14:00 UTC", "1.2K holders"), tanda +/−. Pure function di atas `extract_features`. |
| 0.4 | `backend/tests/test_scorer.py` | Skor deterministik untuk model fixture, top_signals terurut by |kontribusi|, label fitur manusiawi. |

**Selesai jika:** setiap token di feed bisa diberi skor survival dari model run terbaru, beserta 3 signal teratas.

**Status (Oct 3, 2026): kode selesai, belum di-deploy.** Selisih dari rancangan:
- Artifact berupa JSON kanonik (tanpa pickle): booster LightGBM, mean + components PCA lore, `feature_names`, `embedder`. Disimpan di `model_artifacts` (`app/db/model_artifacts_schema.py`, immutable via trigger) dalam transaksi yang sama dengan `model_runs`. sha256 dicek saat load.
- `needs_retrain` juga true jika run terbaru belum punya artifact, jadi setelah deploy worker langsung melatih ulang sekali.
- `scorer.load_live_model` hanya memakai artifact run **terbaru** dan tidak pernah fallback ke model lama. Jika embedder lore di proses berbeda dari saat training, scoring ditolak (`ScorerUnavailable`).
- Top signals dikelompokkan: `launch_hour`, `launch_day`, `holders`, `lore` (PCA + panjang + missing), `name`. Kontribusi dalam log-odds; jumlah grup + bias = logit skor. Lore yang `lore_withheld` tampil sebagai "withheld".

**Temuan baru (perlu keputusan sebelum Fase 1):**
- **F1. `sentence-transformers` tidak ada di `requirements.txt`.** Di produksi embedding lore jatuh ke nol, jadi 24 dari 37 fitur konstan, sementara ε dihitung dengan d = 37. Pilih: tambahkan dependency (image jauh lebih besar, perlu download model), atau keluarkan fitur lore dan turunkan `CAPACITY_D`.
- **F2. Skor tidak terkalibrasi.** Model dilatih dengan `class_weight="balanced"`, jadi `predict_proba` bergeser ke ~0.5 dan bukan peluang survival yang sebenarnya. Threshold entry (K1) harus ditetapkan di atas skala ini, atau ditambah kalibrasi (Platt / isotonic pada CV out-of-fold) sebelum angka "survival probability" dipublikasikan.
- **F3. Holders live vs 48 jam.** Training memakai holders sampel 48 jam; token yang lebih muda di-score dengan jumlah holders yang tersimpan saat itu.

---

## 4. Fase 1: Backend Desk (data + API, mode Gated)

### 1.1 Config (`backend/app/core/config.py` + `.env.example`)

```python
DESK_ENTRY_THRESHOLD: float = 0.65          # K1
DESK_POSITION_SIZE_ETH: float = 0.05        # K2
DESK_MAX_OPEN: int = 3                      # K2
DESK_TP_MC_USD: float = 30_000              # K3
DESK_SL_MC_USD: float = 5_000               # K3
DESK_MAX_HOLD_H: int = 48                   # K3
DESK_MIN_LIQ_USD: float = 5_000             # K4
DESK_START_ETH: float = 1.0
DESK_HEARTBEAT_WARN_S: int = 300
DESK_DROPPED_REVEAL_H: int = 48
DESK_X_POST_DELAY_S: int = 600              # K5
DESK_EXCLUDED_TOKENS: list[str] = [...]     # K8, dari env CSV
```

### 1.2 Schema (`backend/app/db/epochs_schema.py`, SQL idempoten)

```sql
-- Siklus hidup kandidat. Identitas token TIDAK pernah keluar lewat API sebelum status 'open'.
CREATE TABLE IF NOT EXISTS desk_candidates (
  id            BIGSERIAL PRIMARY KEY,
  slot          INT NOT NULL,                 -- nomor anonim "Candidate #3"
  token         TEXT NOT NULL,
  queued_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  survival      DOUBLE PRECISION NOT NULL,
  stage         TEXT NOT NULL CHECK (stage IN ('liquidity_check','sizing','entering','open','dropped')),
  dropped_reason TEXT,
  dropped_at    TIMESTAMPTZ,
  decision_id   BIGINT REFERENCES golem_trade_decisions(id)
);

-- Why disimpan saat keputusan, append-only, dengan hash.
ALTER TABLE golem_trade_decisions ADD COLUMN IF NOT EXISTS why_json JSONB;
ALTER TABLE golem_trade_decisions ADD COLUMN IF NOT EXISTS why_sha256 TEXT;

-- Posisi: dibuka saat buy tx terkonfirmasi, ditutup saat sell tx terkonfirmasi.
CREATE TABLE IF NOT EXISTS desk_positions (
  id            TEXT PRIMARY KEY,             -- 't_0007'
  token         TEXT NOT NULL,
  entry_decision_id BIGINT NOT NULL REFERENCES golem_trade_decisions(id),
  entry_tx      TEXT NOT NULL REFERENCES golem_swaps(tx_hash),
  exit_tx       TEXT REFERENCES golem_swaps(tx_hash),
  exit_reason   TEXT CHECK (exit_reason IN ('take_profit','stop_loss','max_hold','manual_pause')),
  reached_30k   BOOLEAN                      -- diisi setelah 48 jam dari label pipeline
);

-- Heartbeat + state
CREATE TABLE IF NOT EXISTS desk_state (
  id INT PRIMARY KEY CHECK (id = 1),
  state TEXT NOT NULL,
  last_decision_at TIMESTAMPTZ
);
```

- **Immutability `why` di level DB:** trigger yang menolak `UPDATE` pada `why_json` / `why_sha256` jika sudah non-null (pola sama dengan trigger epoch complete).
- `why_sha256 = sha256(canonical_json(why))` — `json.dumps(sort_keys=True, separators=(",", ":"))`. Hash juga ditulis ke log stdout saat entry dan dipublikasikan di kartu Why, supaya publik bisa verifikasi ulang.

### 1.3 Domain logic (`backend/app/services/desk.py`, baru, pure functions)

- `derive_state(gate, open_positions, waiting) -> "gated"|"watching"|"waiting"|"entering"|"in_position"|"paused"`
  - Gated jika Epoch II belum complete; Paused jika `golem_paused` setelah unlock; lalu In position > Entering > Waiting > Watching.
- `anonymize_waiting(candidates) -> [{"slot", "survival", "stage"}]` — **whitelist field**, bukan blacklist. Kandidat `dropped` hanya menampilkan `token` bila `dropped_at + 48h < now`.
- `build_why(...)` — merakit dict sesuai skema brief + `size_rule`, `exit_plan`.
- `position_pnl(entry_eth, token_amount, mark_price) ` dan pemakaian `compute_results` untuk realized PnL. Semua dari `golem_swaps` (onchain), bukan dari log internal.
- `watching_rows(tokens, scores)` — sort by survival desc, status `scoring`/`below_threshold`. **Tidak boleh** ada field yang bergantung pada `desk_candidates` (lihat aturan "queued").

### 1.4 Chain reads (`backend/app/services/chain_reader.py`)

- `get_balance(address)` → saldo ETH wallet.
- `get_reserves(pair)` → mark price + likuiditas (reuse logika USD dari `onchain_dataset.py`).
- Cache 5 detik per pair untuk Open positions.

### 1.5 API (`backend/app/api/desk_endpoints.py`, baru)

- `GET /api/desk` — snapshot penuh sesuai skema brief, cache 5s. Disusun **hanya** lewat `desk.py` serializer (satu jalur keluar data).
- `GET /api/desk/trades/{id}` — detail + kartu Why + `why_sha256`. 404 untuk id yang belum `open` (supaya id kandidat pun tidak bocor).
- Daftarkan router di `backend/app/main.py`.

### 1.6 WebSocket (`backend/app/services/desk_events.py`)

Event mengikuti format key yang sudah ada: `{"desk_state": {...}}`, `{"desk_waiting": {...}}`, `{"desk_open": {...}}`, `{"desk_close": {...}}`. Payload dibangun dengan serializer yang sama dengan `/api/desk`. `desk_open` hanya dipanggil dari indexer setelah buy tx `golem_swaps` muncul (= terkonfirmasi), bukan dari executor saat mengirim tx.

### 1.7 Tests (`backend/tests/test_desk.py`)

- **Anonimisasi:** buat kandidat dengan nama/ticker/alamat unik → assert string-string itu tidak muncul di `json.dumps(/api/desk)` maupun di semua payload `desk_*` untuk stage `liquidity_check`, `sizing`, `entering`, `dropped` (<48j). Assert baris Watching identik sebelum/sesudah token masuk antrean.
- **PnL:** fixture swap onchain → PnL selisih < 0.1% dari perhitungan manual; loss tampil dengan tanda −.
- **Immutability why:** hash cocok; update kedua ditolak (unit pada fungsi + test SQL trigger jika DB test tersedia).
- **State machine:** gated/paused/watching/waiting/in_position dari kombinasi input.
- **Heartbeat:** `last_decision_at` > 300 detik → flag `stale: true`.

**Selesai jika:** `/api/desk` mengembalikan snapshot mode Gated dengan Watching berisi skor live, `blocked_by` sama dengan `/api/state`, dan `test_desk` hijau.

**Status (Oct 3, 2026): kode selesai, belum di-deploy, SQL belum dijalankan ke DB mana pun.** Selisih dari rancangan:
- **Tidak ada tabel `desk_positions`.** Trade dibangun ulang dari `golem_swaps` (`desk.group_trades`): trade dibuka oleh buy saat token belum dipegang dan ditutup saat sisa ≤ 0.1% dari yang dibeli. ID `t_0001…` mengikuti urutan entry dan stabil karena histori swap append-only. Alasan exit dan Why diambil dari `golem_trade_decisions` lewat `tx_hash`.
- **Slot Waiting tidak menampilkan survival.** Watching menampilkan skor persis tiap token, jadi "Candidate #3 · survival 0.71" bisa langsung dicocokkan ke satu token di Watching. Slot hanya berisi nomor, stage, dan waktu (semua slot sudah pasti ≥ threshold). **Perlu konfirmasi @ronald** karena berbeda dari copy di brief.
- `why` disimpan sebagai teks kanonik (`why_canonical`), bukan JSONB, karena JSONB menulis ulang urutan key sehingga hash tidak bisa dihitung ulang. Trigger `golem_trade_decisions_append_only`: hanya `tx_hash` kosong yang boleh diisi; delete ditolak. `record_trade_decision` sekarang wajib membawa Why untuk buy dan `exit_reason` untuk sell, dan mencetak hash ke log.
- `desk_worker` (tiap 60s, advisory lock): skor feed Watching ke `desk_scores`, heartbeat (`last_decision_at` = siklus scoring terakhir; tidak maju jika model belum punya artifact), stage kandidat → `open` setelah swap terindeks, `desk_announcements` (sekali per trade per jenis, tahan restart, nanti jadi antrian X), event `desk_state` / `desk_waiting` / `desk_open` / `desk_close` dengan serializer yang sama dengan REST.
- Watching = token `pending` (≥ $10K, belum dilabel) berumur < 48 jam, hanya skor dari run terbaru. Token tim ditandai `excluded`.
- PnL: realized dari jumlah ETH di receipt; open = nilai sisa token pada harga mid pair WETH (`getReserves`) dikurangi cost basis sisa. Gas tidak dihitung. Jika RPC gagal, harga dan saldo `null`, `pnl.complete = false` (tidak pernah diisi 0).
- `epc_burned` per trade dan `usd` masih `null` sampai K6 dan sumber harga EPC diputuskan.
- Saat deploy pertama, semua trade lama di `golem_swaps` diumumkan sekali lewat WS (belum ada poster X, jadi aman).

---

## 5. Fase 2: Frontend `/desk`

| Task | File | Detail |
| --- | --- | --- |
| 2.1 | `frontend/src/components/layout/HeaderBar.tsx` | Tambah `{ name: 'Desk', href: '/desk' }` setelah Epochs. |
| 2.2 | `frontend/src/app/desk/page.tsx` + `layout.tsx` | Metadata, hero "The Desk. Watch Golem work.", intro. |
| 2.3 | `frontend/src/hooks/useDesk.ts` | Fetch `/api/desk`, poll fallback 30s, subscribe event `desk_*` lewat WS yang sudah ada di store. |
| 2.4 | `frontend/src/store/useEmileStore.ts` | Slice `desk` + reducer per event (tambah/hapus waiting slot, pindah open→closed). |
| 2.5 | `frontend/src/components/desk/StatusBar.tsx` | Sticky; state + copy per state, saldo, start 1 ETH, net PnL (± tanda), EPC burned + link burn address, heartbeat "last decision 12s ago" + warning > 5 menit. Golem pose per state (reuse aset/video golem bila ada; fallback SVG sederhana). |
| 2.6 | `components/desk/WatchingPanel.tsx`, `WaitingPanel.tsx` | Dua kolom di `lg:`; Waiting menampilkan slot anonim + copy "Token revealed after entry confirms…", label "Dropped: <alasan>". |
| 2.7 | `components/desk/OpenPositions.tsx`, `ClosedTrades.tsx` | Full width; tabel di desktop, kartu di mobile. Tidak ada filter default; win/loss sama tebal. Empty state: "No trades yet…". |
| 2.8 | `components/desk/WhyCard.tsx` | Modal/drawer: survival vs threshold, top signals, model run ID, proven floor, size rule, exit plan, hasil + reached $30K, hash `why`. |
| 2.9 | `components/desk/LiveNumber.tsx` | Monospace, flash sekali saat berubah, dimatikan di `prefers-reduced-motion`. |
| 2.10 | `globals.css` | Token `--profit` (sage, sama dengan "passed") dan `--loss` (bata, sama dengan "stalled"); pakai palette yang sudah ada `#2A1C0D/#332311/#E99F30/#FEFAF0`. |
| 2.11 | `components/desk/DeskFooter.tsx` | Golem wallet + agent key (dengan perannya), Blockscout, `/api/desk`, disclaimer. |

**Aturan:** tidak ada fallback angka palsu (pelajaran dari T4 di plan Epochs). Jika API gagal, tampilkan "unavailable".

**Selesai jika:** `/desk` tampil di desktop & mobile (375px) tanpa horizontal scroll, mode Gated menampilkan `blocked_by`, dan event WS yang disimulasikan memperbarui panel tanpa reload.

---

## 6. Fase 3: Worker X auto-post (`backend/app/services/desk_poster.py`)

- Subscribe internal ke `desk_open` / `desk_close` (antrian in-process atau tabel `desk_post_queue (trade_id, kind, due_at, attempts, posted_tweet_id)` — **disarankan tabel**, supaya delay & retry selamat dari restart).
- Template dirender dari **objek `why` yang sama** dengan kartu Why (satu fungsi `render_entry_post(why, trade)` / `render_exit_post(trade)`).
- Sanitizer: hapus `$` di depan `EPC` dan ticker apa pun (`re.sub(r"\$(?=[A-Za-z])", "", text)`) + test.
- Retry 3x dengan backoff, lalu log gagal; tidak pernah memblokir trade.
- Hormati `TWITTER_AUTO_POST_ENABLED`; reuse `TwitterService.post_tweet` (trigger_type `desk_entry` / `desk_exit`).
- Test: template berisi link tx + `/desk` + disclaimer, tanpa `$`, retry berhenti di 3.

---

**Status Fase 2 (Oct 3, 2026): selesai, diverifikasi di browser dengan fixture** (desktop 1366px dan mobile 375px tanpa scroll horizontal; mode Gated, In position, kartu Why, Dropped). Pose Golem masih memakai satu klip (mengetik) dengan treatment berbeda per state; ilustrasi pose terpisah (tidur, mengamati, siaga) belum ada.

**Status Fase 3 (Oct 3, 2026): kode selesai, belum di-deploy.**
- Antrian = `desk_announcements` (kolom `post_status`, `post_attempts`, `tweet_id`, `post_error`, `posted_at`, `event_at`). Delay `DESK_X_POST_DELAY_SECONDS` (default 600), maksimal 3 percobaan, lalu `failed` dan dicatat di log.
- Template dirender dari detail trade yang sama dengan `/api/desk/trades/{id}`; tanpa `$` di depan ticker apa pun (`$30K` tetap). Panjang dihitung seperti X (URL = 23) dan signal dipangkas jika lewat 280.
- Trade yang ditemukan saat deploy pertama (> `DESK_X_POST_MAX_AGE_H` jam) ditandai `skipped`, tidak pernah diposting.
- `DESK_X_POST_ENABLED=false` secara default. Live hanya jika `TWITTER_AUTO_POST_ENABLED` juga true; selain itu `dry_run`.
- `TwitterService._send` dipisah dari `post_tweet`: post Desk (`target_token='desk'`) tidak mengubah cooldown 110 menit maupun rotasi token post berita.

## 7. Fase 4: Golem Executor (blocked oleh K1–K9)

**Status (Oct 3, 2026): kode selesai dalam mode `off` / `dry_run`. Mode `live` sengaja ditolak sampai ada signer.**
- `app/services/desk_trading.py` (pure, teruji): seleksi kandidat, cek likuiditas + MC di antara SL dan TP, sizing (fixed, maks 20% wallet, sisakan gas), exit rule (SL → TP → batas waktu; batas waktu tetap jalan tanpa harga), `getAmountOut` V2, calldata swap V2 (selector diverifikasi dengan keccak).
- `app/services/desk_executor.py`: satu stage per siklus (liquidity_check → sizing → entering) supaya kandidat terlihat di Waiting; exit jalan tiap siklus, juga saat Paused. `record_trade_decision` sekarang hanya menahan **buy** saat gate tertutup.
- Token yang di-drop tidak dicoba lagi sampai lewat jendela reveal 48 jam. Log executor hanya menyebut nomor slot.
- `register_signer` hanya menerima signer dari `GOLEM_WALLET`. Belum ada implementasi signer (perlu K7 dan tempat menyimpan key).
- Dry-run ke data asli (read-only): gate tertutup (`epoch_ii_not_complete`), belum ada token terskor.

**Temuan dari data asli (blocker baru):**
- **K11. DEX.** Dari 30 token teratas di feed, hanya 9 yang punya pair, dan semuanya di **Uniswap v3 (2) / v4 (7)**, tidak ada yang di V2. Executor (dan deteksi swap Epoch III, yang mengandalkan router V2) tidak bisa memperdagangkan feed ini. Perlu diputuskan: dukung v4 (Universal Router + Permit2 + PoolManager), atau batasi Golem ke token yang punya pair V2.
- **K12. Saldo Golem wallet 0 ETH.** `DESK_START_ETH = 1.0` belum tercermin onchain. Selama itu, net PnL % relatif ke 1 ETH yang belum ada.
- Likuiditas feed sangat lebar ($4 sampai $15K), jadi filter K4 memang wajib.

## 7a. Temuan data (Oct 3, 2026). Blocker untuk model, Epoch I/II, dan Desk

- **Holders di DB adalah angka acak.** `holder_sampler.sample_token_holders_rpc` memanggil Helius (API Solana) dengan alamat EVM, dan tanpa `CLEAN_HELIUS_API_KEY` (kosong) mengembalikan `40 + random() ** 2.4 * 2600`. Semua holders stalled (571) dan passed (331) bukan data nyata. **Diperbaiki ke depan:** sampler sekarang menghitung holders onchain di blok launch + 48 jam (`app/services/live_holders.py`) dan mengembalikan `None`, bukan angka karangan, kalau chain tidak terbaca. Data lama belum diubah. Kode lama di Railway masih menulis angka acak sampai di-deploy ulang.
- **Sebagian besar data berlabel bukan token Robinhood Chain.** Dicek dengan `eth_getCode`: stalled 0 dari 571 punya kontrak; passed 159 dari 587 (411 tanpa kontrak, 17 nama NEAR `*.near`); pending 134 dari 178. Kemungkinan besar dari script seed (`seed_2200_tokens.py`, `add_600_tokens_185_passed.py`, `adjust_passed_to_*.py`). Model run 11 (n=2069, AUC 0.71) dan Epoch I dihitung dari data ini. **Perlu keputusan tim:** bersihkan dataset (hanya token dengan kontrak di 4663), label ulang, lalu latih ulang. Tidak ada data yang dihapus.
- **`tokens.launched_at` bukan waktu pembuatan token**, melainkan waktu feed pertama melihatnya (DIVIX: transfer pertama ~42 jam sebelum `launched_at` 20 jam). Penghitungan holders tidak bergantung padanya (jendela 10 juta blok).
- **Desk:** holders "now" dihitung onchain per token (`desk_live_holders`, 40 token per siklus) dan entri tanpa kontrak disembunyikan dari Watching (jumlahnya ditampilkan). Skor tetap ~0.99 untuk semua token, karena model belajar "holders sedikit = passed" dari data di atas. Panel Watching menyatakan skornya belum bisa diandalkan.
- **RPC:** `RH_MAINNET_RPC_URL` (Alchemy, chain 4663) dipakai untuk pembacaan titik dan sebagai cadangan hitung holders (`alchemy_getAssetTransfers`, hasilnya identik dengan replay log). Free tier Alchemy membatasi `eth_getLogs` ke 10 blok dan compute unit per detik (429 di body JSON, sekarang di-retry), jadi scan log tetap memakai RPC publik (maks 10 juta blok dan 10 ribu log per panggilan).

## 7b. Temuan saat dijalankan dengan data asli (Oct 3, 2026)

- **Model membocorkan label lewat holders yang kosong.** Di data training, holders kosong hanya terjadi pada token `passed` (30% dari passed, 0% dari stalled), karena token itu menembus $30K sebelum sampel holders 48 jam. Model membaca "holders kosong" sebagai "passed", sehingga setiap token live (holders belum disampel) mendapat survival ≈ 1.0. AUC 0.71 kemungkinan ikut membengkak karena ini. **Sementara:** scorer tidak memberi skor pada token tanpa holders (status `awaiting_holders`) dan skor lama dihapus. **Perlu keputusan tim:** fitur holders 48 jam tidak tersedia saat entry (< 48 jam), jadi untuk model trading fitur ini perlu dibuang atau diganti dengan holders pada umur tetap yang lebih muda; setelah itu `CAPACITY_D` dan methodology ikut berubah.
- **Advisory lock bocor** di model worker, epoch watcher, dan desk worker: unlock sering jalan di koneksi pool yang berbeda, sehingga siklus berikutnya mengira ada instance lain. Diganti dengan lock tingkat transaksi pada satu koneksi (`app/db/locks.py`); penerapan schema juga diserialkan (sebelumnya deadlock saat startup).
- **Dua backend di satu DB.** `epochlabs-production.up.railway.app` (kode lama, `/api/desk` 404) memakai DB yang sama dan mencoba tweet tiap ~5 menit sejak paling tidak 08:05 (semua gagal HTTP 402, kredit X habis). Ditambahkan `INGEST_WORKER_ENABLED` dan `TWITTER_SCHEDULER_ENABLED`; backend lokal berjalan dengan ingest, Twitter scheduler, dan epoch watcher mati, sehingga hanya Railway yang menjalankan writer tersebut. Spam tweet baru berhenti setelah Railway di-deploy ulang atau `TWITTER_AUTO_POST_ENABLED` dimatikan di Railway.

Ini bagian yang sebenarnya mengisi Waiting/Open. Sebaiknya brief terpisah, tapi garis besarnya:

1. **Selector** (`desk_executor.py`, loop 30–60s): ambil token Watching dengan skor ≥ threshold, tidak dikecualikan (K8), belum dipegang, `open < DESK_MAX_OPEN` → insert `desk_candidates` (slot anonim) → broadcast `desk_waiting`.
2. **Entry checks:** likuiditas (K4), simulasi slippage, gate (`record_trade_decision` + `why_json` + hash) → stage `entering`.
3. **Send:** sign & kirim swap dari wallet yang disepakati (K7), lewat RPC MEV-protected bila ada (K9), dengan `amountOutMin` ketat dan deadline pendek. `attach_trade_tx`.
4. **Konfirmasi:** indexer yang sudah ada menangkap swap → buat `desk_positions` → `desk_open`.
5. **Exit loop:** cek mark MC per posisi tiap tick → TP / SL / max hold → sell → `desk_close` (+ burn sesuai K6).
6. **Paused:** saat `golem_paused`, selector berhenti, exit loop tetap jalan (sesuai brief).
7. **Safety:** kill-switch env `DESK_EXECUTOR_ENABLED=false` default; dry-run mode yang menulis keputusan tanpa mengirim tx (bisa dipakai menguji Desk end-to-end sebelum epoch III); backtest dulu di dataset `onchain_dataset.py` untuk memvalidasi K1–K4.

---

## 8. Urutan kerja & estimasi kasar

| Fase | Isi | Bergantung pada | Estimasi |
| --- | --- | --- | --- |
| 0 | Persist model + scorer + top signals | — | 1–1.5 hari |
| 1 | Schema, desk.py, API, WS, test_desk | Fase 0 | 2 hari |
| 2 | Halaman `/desk` + komponen | Fase 1 (bisa paralel pakai mock payload) | 2–3 hari |
| 3 | X auto-post worker | Fase 1, K5 | 0.5–1 hari |
| 4 | Executor (dry-run dulu, lalu live) | K1–K9, Epoch II complete | 3–5 hari + backtest |

Fase 0–3 menghasilkan Desk yang live dalam mode Gated dan memenuhi acceptance criteria #1, #2, #3, #6, #7 (via simulasi), #9, #10. Kriteria #4, #5, #8 baru bisa diverifikasi end-to-end setelah trade pertama (Fase 4, atau dry-run).

---

## 9. Pemetaan acceptance criteria

| Kriteria | Dipenuhi oleh |
| --- | --- |
| `/desk` di nav, desktop + mobile tanpa h-scroll | 2.1, 2.2, 2.7 |
| Mode Gated + `blocked_by` | 1.3 `derive_state`, 2.5 |
| Tidak ada identitas kandidat di API/WS sebelum konfirmasi (test) | 1.3 `anonymize_waiting`, 1.6, 1.7 |
| PnL sesuai onchain < 0.1% | 1.3, 1.4, 1.7 |
| Kartu Why lengkap + hash cocok | 1.2 trigger + hash, 2.8 |
| Loss sama jelas, tanpa filter default | 2.7 |
| `desk_open`/`desk_close` update tanpa reload | 1.6, 2.3, 2.4 |
| Post X setelah delay, isi = kartu Why, tanpa `$` | Fase 3 |
| Peringatan heartbeat > 5 menit | 1.3, 2.5 |
| `test_desk` (anonimisasi, PnL, immutability) | 1.7 |
