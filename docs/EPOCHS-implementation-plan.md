# Implementation Plan: Epochs Page

Oct 3, 2026 · berdasarkan *Developer Brief: Epochs Page* (@ronald)

Dokumen ini memetakan brief ke codebase saat ini: apa yang sudah ada, apa yang memblokir, dan urutan kerja per fase lengkap dengan file yang disentuh.

---

## 1. Temuan audit (brief vs. kode sekarang)

### Blocker: harus beres sebelum Epoch I/II bisa ditampilkan jujur

| # | Temuan | Lokasi | Dampak ke brief |
| --- | --- | --- | --- |
| B1 | Tidak ada worker yang menjalankan training. `train_model_and_evaluate` hanya dipanggil dari script manual (`adjust_passed_to_*.py`, `add_*`). | `backend/app/db/*.py`, `backend/app/services/ingest_worker.py` | Prinsip 2 ("status dihitung dari data model") tidak terpenuhi selama `model_runs` hanya terisi manual. |
| B2 | Jar level di-overwrite manual: `UPDATE model_runs SET jar_level = 0.80`. | `backend/scratch/update_jar_80.py` | Penyebab sand level 80% yang tidak sesuai rumus (Open question #1). |
| B3 | `raw_jar_level` dihitung dari `auc_mean`, bukan `proven_floor`. | `backend/app/ml/jar_math.py:72` | Hourglass bisa terlihat penuh walaupun floor yang terbukti masih jauh di bawah 0.60. |
| B4 | ε yang tersimpan (0.042) tidak cocok dengan rumus. Untuk n=1570, d=28 hasilnya ε ≈ 0.324, jadi floor_vc ≈ 0.304. | `docs/db_export/model_runs.md`, `jar_math.calculate_epsilon_vc` | Epoch II ("floor ≥ 0.60") secara jujur masih sangat jauh. |
| B5 | Nilai `d` tidak konsisten: trainer dan config memakai 28, sedangkan `/api/methodology.json` dan default frontend memakai 41. | `trainer.py:121`, `config.py`, `endpoints.py:162`, `useEmileDatabase.ts` | Angka di halaman Epochs bisa berbeda dengan The Math (melanggar Prinsip 5). |
| B6 | Gate berbeda di tiga tempat: backend 2000/200/σ<0.05/gap≤0.04, frontend 1000/300/σ≤0.02 (hardcode). | `jar_math.py:76`, `LearningProgressSection.tsx:76-78` | Open question #6. Trigger Epoch I/II butuh satu sumber kebenaran. |

### Selisih teknis lainnya

| # | Temuan | Catatan |
| --- | --- | --- |
| T1 | Event WS `model` belum ada di backend. Frontend sudah mendengarkan `payload.model`, tapi backend hanya mengirim `{"token": ...}`. | Format WS berbasis key. Event baru mengikuti pola yang sama: `{"epoch": {...}}`. |
| T2 | Belum ada integrasi EVM. `holder_sampler.py` masih memakai Helius (Solana). | Perlu client JSON-RPC atau Blockscout untuk Robinhood Chain (4663). |
| T3 | `burn_address` masih null. Launcher, agent, wallet, dan template belum ada di config. | Ditambahkan ke `config.py` dan `constants.ts` lewat env. |
| T4 | Frontend punya fallback angka palsu (`proven_floor \|\| 0.6743`, `'80.0'`, store default `jar_level: 0.95`). | Halaman Epochs tidak boleh memakai fallback. Jika data kosong, tampilkan state "unavailable". |
| T5 | Tidak ada migration tool. Schema dikelola di `DDL.sql` dan `execute_ddl.py`. | Tabel baru ditambahkan di DDL dengan `CREATE TABLE IF NOT EXISTS`. |
| T6 | Stack frontend: Next 16 App Router, Zustand, Tailwind 4, tanpa library animasi. | Animasi sand pour memakai CSS/SVG. Tidak perlu dependency baru. |

---

## 2. Keputusan yang dibutuhkan (owner: tim / @ronald)

Fase 1 bagian logika murni bisa jalan tanpa ini, tapi trigger nyata tidak bisa diaktifkan sebelum semuanya terjawab.

- [ ] **D1. Gate resmi** untuk Epoch I/II: 2000 / 200 / 0.05 (methodology) atau 1000 / 300 / 0.02 (dashboard)?
- [ ] **D2. Capacity `d`**: 28 atau 41?
- [ ] **D3. Rumus jar**: diskalakan dari `proven_floor` (disarankan) atau dari `auc_mean`?
- [ ] **D4. Alamat Epoch III**: swap dari `golem_wallet` (`0x49Ed…582C`) atau signer `agent` (`0x560E…14aD`)? Disarankan: tx `from == golem_wallet`, `to == UniswapV2Router`, lalu jelaskan peran agent sebagai signer EIP-712 di halaman.
- [ ] **D5. Burn address** dan aturan burn: per trade atau net per periode? Pakai high-water mark?
- [ ] **D6. Aturan Golem Launch** (fee, pembelian awal oleh tim, batas modal) dipublikasikan sebelum Epoch V dibuka.
- [ ] **D7. Epoch VI**: Open Golem atau Multisig Handoff (`OwnershipTransferred` dari `0x2f96…3bd1`)?
- [ ] **D8. Infra**: URL RPC Robinhood Chain, base URL API Blockscout, alamat Uniswap V2 Router, dan repo GitHub untuk release.
- [ ] **D9. Bootstrap**: apakah epoch yang triggernya sudah terpenuhi di masa lalu boleh di-backfill otomatis oleh watcher (dengan proof historis)? Disarankan: ya, lewat scan dari block deploy.

---

## 3. Fase 0: Perbaikan fondasi model

Tujuannya agar angka floor, sand level, dan gate punya satu sumber dan dihitung oleh sistem, bukan diinput manual.

| Task | File | Detail |
| --- | --- | --- |
| 0.1 | `backend/app/core/config.py` | Tambahkan `GATE_N_SAMPLES`, `GATE_N_POSITIVE`, `GATE_AUC_STD_MAX`, `GATE_TIME_SPLIT_GAP_MAX`. `CAPACITY_D` mengikuti D2. |
| 0.2 | `backend/app/ml/jar_math.py` | Gate membaca dari `settings`. Rumus jar mengikuti D3. Default `d` diambil dari `settings.CAPACITY_D`. |
| 0.3 | `backend/app/ml/trainer.py` | Pakai `settings.CAPACITY_D`, jangan literal `d=28`. |
| 0.4 | `backend/app/api/endpoints.py` | `/api/methodology.json` membaca gate dan `d` dari `settings`. Tambahkan `gates_config` ke `/api/state` supaya frontend tidak hardcode. Ekstrak helper `get_latest_model(db)` untuk dipakai `/api/state` dan `/api/epochs`. |
| 0.5 | `backend/app/services/model_worker.py` (baru) | Loop berkala (mis. tiap 1 jam): train, simpan `ModelRun`, broadcast `{"model": {...}}`, lalu invalidasi cache `/api/state`. Didaftarkan di `lifespan` (`main.py`). |
| 0.6 | `backend/scratch/update_jar_80.py` | Hapus. Tambahkan catatan di README bahwa `model_runs` tidak boleh diedit manual. |
| 0.7 | `frontend/src/components/analytics/LearningProgressSection.tsx`, `ProofPanel.tsx` | Gate dibaca dari `/api/state`, dan fallback angka palsu dihapus. *(Bisa jadi ticket terpisah, tapi dibutuhkan untuk acceptance "angka sama".)* |
| 0.8 | `backend/tests/test_jar_math.py` | Update tes: rumus ε untuk n=1570, d=28 ≈ 0.324, jar dihitung dari floor, gate dari config. |

**Selesai jika:** `/api/state`, `/api/methodology.json`, dan The Math menampilkan gate, `d`, floor, dan sand level yang sama, dan model run baru dibuat otomatis.

**Status (Oct 3, 2026): kode selesai, belum di-deploy.** Default yang dipakai sambil menunggu D1–D3:
- D1: gate methodology, yaitu 2000 / 200 / σ<0.05 / gap ≤0.04 (`settings.GATE_*`).
- D2: `d = 37`, sama dengan jumlah kolom yang benar-benar dihasilkan `extract_features`. Trainer menolak jalan jika tidak cocok.
- D3: jar = `clip((proven_floor − 0.50) / 0.10)`, dicap 0.95 selama ada gate yang gagal.

Setelah deploy, model worker otomatis melatih ulang karena run lama memakai `capacity_d = 28`. Angka publik akan turun drastis (ε ≈ 0.3, jar ≈ 0%). Itu angka yang jujur, jadi perlu dikomunikasikan sebelum deploy.

---

## 4. Fase 1: Backend Epochs

**Status (Oct 3, 2026): kode selesai, belum di-deploy.** Selisih dari rancangan di bawah:
- Router, owner, dan deploy block diambil dari `contracts/deployments/robinhood.json`, jadi D8 sebagian besar terjawab.
- Blockscout API tertahan challenge Cloudflare, sehingga semua data diambil lewat JSON-RPC saja. Swap Golem dideteksi dari log `Transfer` yang menyentuh wallet. Batas node 30k block per `getLogs` tanpa alamat.
- Bukti signer Epoch V tidak perlu decode calldata. Kontrak hanya meng-emit `Launched` setelah memverifikasi signature dari `agent`, jadi cukup replay `AgentUpdated`.
- Schema berupa SQL idempoten di `app/db/epochs_schema.py` (bukan ORM) dan diterapkan saat watcher start.
- Default D4: Epoch III = tx `from == golem_wallet`, `to == router`. Default D5: burn hanya dihitung jika `from == golem_wallet`.
- Bukti untuk Epoch I/II hanya run dengan `notes LIKE 'model_worker%'` dan `capacity_d` saat ini. Run lama dari script tidak dihitung.
- Bukti onchain harus terjadi setelah epoch sebelumnya complete.

### 1.1 Config (`backend/app/core/config.py`)

```python
CHAIN_ID: int = 4663
CHAIN_RPC_URL: str = os.getenv("CHAIN_RPC_URL", "")
BLOCKSCOUT_BASE: str = os.getenv("BLOCKSCOUT_BASE", "https://robinhoodchain.blockscout.com")
EPOCH_LAUNCHER: str = "0x75fd64Cc8D57c529f34089Ac9083E704c23F0D8B"
EPOCH_TOKEN_TEMPLATE: str = "0x96508719c110a341708546de78051e020f51D264"
GOLEM_AGENT: str = "0x560Eb3767434006b3278810f906d7677C38914aD"
GOLEM_WALLET: str = "0x49EdF5f24216e02EEb6a947cC3dF0CDB6B84582C"
EPC_BURN_ADDRESS: str | None = os.getenv("EPC_BURN_ADDRESS") or None
UNISWAP_V2_ROUTER: str = os.getenv("UNISWAP_V2_ROUTER", "")
GOLEM_GITHUB_REPO: str = os.getenv("GOLEM_GITHUB_REPO", "")
EPOCH_WATCHER_INTERVAL_SECONDS: int = 60
```

Semua alamat bisa di-override lewat env. Tambahkan juga ke `backend/.env.example`.

### 1.2 Schema (`backend/DDL.sql` + `backend/app/db/models.py`)

```sql
CREATE TABLE IF NOT EXISTS epochs (
  id            SMALLINT PRIMARY KEY CHECK (id BETWEEN 1 AND 6),
  key           TEXT NOT NULL UNIQUE,
  status        TEXT NOT NULL DEFAULT 'locked' CHECK (status IN ('locked','active','complete')),
  proof_json    JSONB,
  completed_at  TIMESTAMPTZ,
  CHECK (status <> 'complete' OR (proof_json IS NOT NULL AND completed_at IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS chain_cursor (
  name        TEXT PRIMARY KEY,        -- 'golem_wallet', 'epc_transfer', 'launcher'
  last_block  BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS epc_burns (
  tx_hash     TEXT NOT NULL,
  log_index   INT NOT NULL,
  block       BIGINT NOT NULL,
  amount_wei  NUMERIC(78,0) NOT NULL,
  at          TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tx_hash, log_index)
);
```

- Seed enam baris `epochs` dengan status `locked`.
- **Permanen di level DB:** trigger Postgres yang menolak `UPDATE` jika `OLD.status = 'complete'` (pola yang sama dengan `scratch/add_postgres_trigger.py`).
- Status `active` **tidak disimpan**. Status ini diturunkan saat runtime (epoch pertama yang belum complete), jadi tidak mungkin ada dua epoch aktif.

### 1.3 Domain logic (`backend/app/services/epochs.py`, baru)

Berisi fungsi murni tanpa I/O agar mudah diuji.

- `EPOCH_DEFS`: daftar `(id, key, name)` I–VI.
- `derive_statuses(completed: dict[int, Completion]) -> list[EpochView]`
  - Complete jika ada di `completed`. Epoch pertama yang belum complete berstatus `active`, sisanya `locked`.
- `can_complete(epoch_id, completed) -> bool`: semua `id < epoch_id` harus sudah complete.
- `validate_proof(epoch_id, proof) -> None | raise`
  - I/II: `{"type": "model_run", "run_id": int}`. II juga wajib menyertakan `methodology_snapshot` (hasil `/api/methodology.json` saat itu).
  - III/IV: `{"type": "tx", "hash": ^0x[0-9a-f]{64}$, "url": BLOCKSCOUT_BASE + "/tx/" + hash}`.
  - V: seperti tx, ditambah `token_address` (`^0x[0-9a-fA-F]{40}$`).
  - VI: `{"type": "release", "url": "https://github.com/<repo>/releases/tag/golem-v1"}`.
- `progress_for(epoch_id, model) -> dict | None`
  - I: `{"current": model.n_samples, "target": GATE_N_SAMPLES, "unit": "samples"}`.
  - II: `{"current": model.proven_floor, "target": 0.60, "unit": "floor", "jar_level": ..., "gates": ..., "blocked_by": ...}`.
  - III–VI: `None`.
- `golem_paused(model, completed) -> str | None`: mengembalikan nama gate yang gagal jika Epoch II sudah complete tapi `blocked_by` tidak kosong atau `proven_floor < 0.60`.

### 1.4 Trigger checkers

| Epoch | Checker | Sumber |
| --- | --- | --- |
| I | `model.n_samples >= GATE_N_SAMPLES` → proof `run_id` | `get_latest_model()` |
| II | `model.proven_floor >= 0.60 and not model.blocked_by` → proof `run_id` + snapshot methodology | `get_latest_model()` |
| III | Tx pertama `from == GOLEM_WALLET` dan `to == UNISWAP_V2_ROUTER` dengan status sukses | `chain_reader.wallet_txs()` |
| IV | Log `Transfer(address,address,uint256)` pertama pada $EPC dengan `to == EPC_BURN_ADDRESS`. Semua burn juga di-upsert ke `epc_burns`. | `chain_reader.get_logs()` |
| V | Event launch pertama dari `EPOCH_LAUNCHER` yang signer-nya `GOLEM_AGENT` (decode event, atau verifikasi signature EIP-712 dari calldata) | `chain_reader.get_logs()` |
| VI | Release `golem-v1` ada, dan repo punya `LICENSE` (`GET /repos/{repo}/license` → 200) | GitHub REST API |

Catatan: checker IV tetap mengindeks burn meskipun epoch IV belum aktif, supaya total burn akurat sejak awal.

### 1.5 Chain client (`backend/app/services/chain_reader.py`, baru)

- `httpx.AsyncClient` (sudah ada di dependencies). Tidak perlu `web3.py`.
- `eth_blockNumber`, `eth_getLogs` dengan paging per 5k block dan `chain_cursor` agar scan inkremental, serta `eth_getTransactionReceipt`.
- Riwayat tx wallet lewat Blockscout API (`/api/v2/addresses/{addr}/transactions`), karena JSON-RPC tidak menyediakan "tx by address".
- Timeout dan retry dengan backoff. Kegagalan RPC hanya di-log ke `ingest_log` (`source='epoch_watcher'`) dan tidak pernah mengubah status epoch.

### 1.6 Epoch Watcher (`backend/app/services/epoch_watcher.py`, baru)

```
loop every 60s:
  completed = load epochs where status='complete'
  run indexers (burn index selalu jalan)
  active = first not completed
  result = checker[active]()
  if result:
      validate_proof(active, result.proof)
      assert can_complete(active, completed)
      INSERT/UPDATE epochs SET status='complete', proof_json, completed_at = event time (block timestamp / ran_at)
      invalidate /api/epochs cache
      broadcast {"epoch": {"id", "status": "complete", "proof"}}
      continue to the next epoch in the same tick (supports backfill)
```

- Didaftarkan di `lifespan` di `backend/app/main.py`, dengan pola yang sama seperti ingest worker.
- `completed_at` diambil dari waktu event aslinya (timestamp block atau `ran_at` model run), bukan waktu watcher mendeteksinya.
- Pakai advisory lock Postgres (`pg_try_advisory_lock`) supaya aman jika ada lebih dari satu instance (Railway).

### 1.7 API (`backend/app/api/epochs_endpoints.py`, baru)

`GET /api/epochs` dengan cache in-memory 15 detik, mengikuti pola `/api/state`.

```json
{
  "active": 1,
  "golem_paused": null,
  "model": { "run_id": 12, "n_samples": 1570, "proven_floor": 0.304, "jar_level": 0.0, "blocked_by": "n_samples" },
  "epochs": [ { "id": 1, "key": "ingestion", "name": "Ingestion", "status": "active",
                "progress": { "current": 1570, "target": 2000, "unit": "samples" },
                "proof": null, "completed_at": null }, "... 6 item" ],
  "burns": { "total_wei": "0", "count": 0 },
  "contracts": { "chain_id": 4663, "launcher": "…", "token_template": "…", "agent": "…",
                 "golem_wallet": "…", "burn_address": null, "epc_token": "0xb714…353e" }
}
```

- Blok `model` diambil dari `get_latest_model()` yang sama dengan `/api/state`. Frontend tidak boleh menghitung ulang.
- Daftarkan router di `main.py`.

### 1.8 Tests (`backend/tests/test_epochs.py`, baru)

- Urutan: II tidak bisa complete sebelum I, dan III tidak bisa complete walaupun ada tx swap jika II belum complete.
- `derive_statuses`: tepat satu `active`. Semua complete berarti `active = null`.
- Permanen: setelah II complete, model run baru dengan floor 0.55 tidak mengubah status, dan `golem_paused` mengembalikan nama gate.
- `validate_proof`: hash salah format, URL bukan Blockscout, `run_id` tidak ada, atau proof kosong → ditolak.
- `progress_for` I/II sama persis dengan field di `/api/state` untuk model run yang sama.
- Checker III–V memakai fixture log/tx (tanpa network): event dari signer selain agent diabaikan, dan Transfer ke alamat selain burn diabaikan.

---

## 5. Fase 2: Frontend

**Status (Oct 3, 2026): kode selesai, belum di-deploy.** Diverifikasi di preview dengan mock API yang memakai `build_epochs_payload` asli:
- Desktop dan mobile 375px tanpa scroll horizontal.
- Event WS `epoch` mengubah kartu (active → complete, animasi pour) tanpa reload.
- "Golem paused: ‹gate›" muncul setelah floor turun.
- Share `/epochs/first-burn` mengarah ke `#first-burn` dengan OG image live.
- Jika API mati, halaman menampilkan "Epoch data unavailable".
- `next build` lolos.

Selisih dari rancangan:
- Share per epoch memakai route `/epochs/[slug]`, karena fragment `#` tidak pernah sampai ke crawler.
- "N of 6 unlocked" menghitung epoch yang complete. Contoh di brief ("Epoch I active · 1 of 6") terlihat ikut menghitung yang aktif, jadi perlu dikonfirmasi.
- Nav header dibuat wrap di mobile, karena enam tab tidak muat di 375px.

### 2.1 Data layer

| File | Perubahan |
| --- | --- |
| `frontend/src/config/constants.ts` | Tambahkan `CHAIN_ID`, `BLOCKSCOUT_BASE`, dan alamat kontrak (dengan override `NEXT_PUBLIC_*`). Hanya untuk link, bukan untuk status. |
| `frontend/src/store/useEmileStore.ts` | Slice `epochs: EpochsPayload \| null`, `setEpochs`, `applyEpochEvent(evt)`, `lastCompletedId` (pemicu animasi). |
| `frontend/src/hooks/useEpochs.ts` (baru) | Fetch `/api/epochs` saat mount dan refetch tiap 60 detik sebagai fallback. Menggunakan socket yang sama dari `useEmileDatabase`. |
| `frontend/src/hooks/useEmileDatabase.ts` | Tangani `payload.epoch` → `applyEpochEvent`. Tangani `payload.model` → juga refetch `/api/epochs` agar progress I/II sinkron. |

### 2.2 Halaman dan komponen

```
frontend/src/app/epochs/page.tsx
frontend/src/app/epochs/layout.tsx              # metadata + OG default
frontend/src/app/epochs/og/[key]/route.tsx      # OG image per epoch (next/og ImageResponse)
frontend/src/config/epochsCopy.ts               # copy dari brief (teks saja, tanpa status)
frontend/src/components/epochs/EpochsHero.tsx
frontend/src/components/epochs/EpochTimeline.tsx
frontend/src/components/epochs/EpochCard.tsx
frontend/src/components/epochs/EpochIcon.tsx    # hourglass kosong / berjalan / penuh
frontend/src/components/epochs/EpochsFooter.tsx
```

- **Nav:** di `HeaderBar.tsx`, sisipkan `{ name: 'Epochs', href: '/epochs' }` antara The Math dan About.
- **Hero:** title, subtitle, dan intro dari copy. `Hourglass` (yang sudah ada) memakai `model.jar_level`. Baris status: `Epoch {roman(active)} active · {completed} of 6 unlocked`.
- **Timeline:** 6 titik berupa `<a href="#key">` dengan smooth scroll. Garis terisi amber sampai epoch aktif. Di mobile (<768px) menjadi vertikal sticky di kiri.
- **EpochCard:** tiga state dengan ikon, border, dan label teks ("Locked" / "Now" / "Unlocked"), jadi tidak hanya mengandalkan warna.
  - `active`: progress bar dengan `role="progressbar"` dan `aria-valuenow`. Untuk II juga ditampilkan daftar gate.
  - `complete`: link proof (Blockscout / run ID / release) dan `completed_at` dalam format UTC.
  - III + `golem_paused`: banner "Golem paused: ‹gate›".
  - III complete: link ke Trade Journal (route menyusul, lihat Fase 3).
  - IV: total $EPC burned dan jumlah burn dari `burns`.
  - `id` kartu = `key` dengan tanda hubung (`first-burn`) untuk anchor share.
- **Animasi:** transisi `active` → `complete` sekali saja, berupa sand pour sekitar 1.2 detik dengan keyframes CSS di `globals.css`. Animasi dinonaktifkan dengan `@media (prefers-reduced-motion: reduce)`.
- **Typography:** nomor romawi memakai serif besar (kelas yang sama dengan `AgentMathSection` numeral), isi kartu sans, dan trigger `font-mono`.
- **Warna:** pakai token yang sudah ada di `globals.css` (`--banana`, `--panel`, `--rule`, dst.). Jika ada nilai brief yang belum terdaftar (`#3F2C17`), tambahkan sebagai variabel baru.
- **Footer:** link Golem wallet dan kontrak ke Blockscout, link `/api/epochs`, kalimat "Every epoch is verified…", dan disclaimer yang selalu terlihat.
- **Data kosong atau error:** tampilkan "Epoch data unavailable". Jangan pernah fallback ke angka atau status default.

### 2.3 Larangan (dicek saat code review)

- Tidak ada `status: 'complete'` atau `'active'` literal di JSX maupun config.
- Tidak ada perhitungan floor, ε, atau jar di frontend halaman ini.
- Tidak ada kata yang dilarang Prinsip 4. Tambahkan lint sederhana atau tes grep pada `epochsCopy.ts`.

---

## 6. Fase 3: Follow-up (ticket terpisah)

**Status (Oct 3, 2026): kode selesai, belum di-deploy.** Codebase belum punya eksekutor trading, jadi yang dibangun adalah kontrak yang wajib dipakai eksekutor nanti:
- **Guard:** `app/services/golem_guard.py`. `record_trade_decision()` menolak (`GolemPausedError`) jika Epoch II belum complete atau `golem_paused`, lalu mencatat survival probability, top signal, dan run_id. `attach_trade_tx()` menghubungkan keputusan ke tx-nya. Tidak ada endpoint publik untuk menulis. Endpoint baca: `GET /api/golem/status`.
- **Journal:** indexer mendekode receipt swap (side, token, jumlah, ETH via WETH). `GET /api/trades` menghitung hasil per sell dengan average cost per token. Halaman ada di `/epochs/journal`, dan kartu III mengarah ke sana setelah complete.
- **Dashboard:** rate telemetri karangan diganti jumlah asli dari `/api/state` dan status WebSocket yang sebenarnya. Fallback angka di ProofPanel dihapus, dan AgentMathSection memakai ε dari model run.

- **Trade Journal** (dibutuhkan setelah Epoch III complete): `/api/trades` dan halaman yang menampilkan survival probability, signal utama, dan hasil.
- **Golem trading guard:** eksekutor trade wajib mengecek `golem_paused` sebelum swap (penegakan di backend, bukan hanya label UI).
- **Pembersihan dashboard:** hapus semua fallback angka hardcode di `LearningProgressSection`, `ProofPanel`, dan default store.

---

## 7. Pemetaan acceptance criteria

| Acceptance criterion | Dipenuhi oleh |
| --- | --- |
| `/epochs` di nav, desktop + mobile tanpa scroll horizontal | 2.2 Nav, layout mobile, verifikasi di preview 375px |
| Status hanya dari `/api/epochs` | 1.3 `derive_statuses`, 2.3 larangan |
| Progress I dan sand level II sama dengan Hero dan The Math | 0.4 `get_latest_model()`, 1.7 blok `model`, 0.7 |
| Epoch tidak bisa complete sebelum epoch sebelumnya (unit test) | 1.3 `can_complete`, 1.8 |
| Complete selalu punya proof valid dan link Blockscout benar | 1.2 CHECK constraint, 1.3 `validate_proof`, 1.8 |
| Event WS `epoch` mengubah state tanpa reload | 1.6 broadcast, 2.1 `applyEpochEvent` |
| "Golem paused: ‹gate›" di kartu III | 1.3 `golem_paused`, 2.2 |
| Alamat footer = mainnet 4663 | 1.1 config, 1.7 `contracts`, 2.2 footer |
| `test_epochs` (urutan, permanen, proof) | 1.8 |
| Disclaimer selalu terlihat | 2.2 footer |

---

## 8. Urutan kerja dan dependensi

```
Fase 0 (0.1–0.4, 0.8) ──┐
                        ├─► Fase 1.2–1.3 + 1.8 (logika + tes, tanpa jaringan)
D1–D3 dijawab ──────────┘          │
                                   ├─► 1.7 /api/epochs (I/II live, III–VI locked)
                                   │        │
                                   │        └─► Fase 2 frontend (paralel)
D4, D5, D8 dijawab ─► 1.1 + 1.5 + 1.4 (III, IV) ─► 1.6 watcher aktif
D6 dipublikasikan ──► checker V aktif
D7 diputuskan ──────► checker VI (Open Golem atau Multisig Handoff)
0.5 model worker ───► Epoch I/II benar-benar bergerak otomatis
```

**Milestone rilis yang disarankan**

1. **M1:** Fase 0, logika dan tes epochs, `/api/epochs`, dan halaman frontend. Epoch I live, II–VI `locked`. Halaman sudah bisa rilis karena tidak ada status yang dibuat-buat.
2. **M2:** chain reader dan watcher untuk III + IV, ditambah indeks burn.
3. **M3:** checker V (setelah aturan launch dipublikasikan) dan VI, plus OG image per epoch.
