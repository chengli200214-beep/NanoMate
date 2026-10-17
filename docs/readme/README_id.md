# nanoMuse Assistant

Asisten pribadi Windows dengan buku tugas dan modul riset berbasis bukti.

[English](../../README.md) | [简体中文](../../docs/readme/README_zh.md) | [繁體中文](../../docs/readme/README_zh-TW.md) | [Español](../../docs/readme/README_es.md) | [Français](../../docs/readme/README_fr.md) | [Bahasa Indonesia](../../docs/readme/README_id.md) | [日本語](../../docs/readme/README_ja.md) | [한국어](../../docs/readme/README_ko.md) | [Русский](../../docs/readme/README_ru.md) | [Tiếng Việt](../../docs/readme/README_vi.md)

<p>
  <img src="https://img.shields.io/badge/Windows-x64-5d7161" alt="Windows x64">
  <img src="https://img.shields.io/badge/DeepSeek-text-c47759" alt="DeepSeek text">
  <img src="https://img.shields.io/badge/UI-React%20%2B%20TypeScript-6e7f91" alt="React and TypeScript">
  <a href="../../LICENSE"><img src="https://img.shields.io/badge/license-GPL--3.0--or--later-746b62" alt="GPL-3.0-or-later"></a>
  <br>
  <a href="https://github.com/chengli200214-beep/continuous-research-assistant/releases/download/desktop-assistant-v1.0.5/nanoMuse-Assistant-1.0.5-win-x64.exe"><img src="https://img.shields.io/badge/Download-Windows_x64-c47759" alt="Download Windows x64"></a>
  <a href="../../docs/assistant-desktop.md"><img src="https://img.shields.io/badge/Docs-desktop-746b62" alt="Desktop documentation"></a>
  <a href="../../docs/assistant.md"><img src="https://img.shields.io/badge/Docs-task%20ledger-746b62" alt="Task ledger documentation"></a>
</p>

**nanoMuse Assistant** menyatukan percakapan, rencana, pengingat, dokumen, dan riset. Jelaskan tujuan, tinjau langkah, lalu ikuti hasilnya dalam buku tugas persisten. Pengembangan dari [nanoMuse](https://github.com/nano-muse/nanoMuse) ini menambahkan ruang kerja Windows mandiri dan mempertahankan modul riset.

## Tampilan produk

Tampilan desktop saat ini memakai profil luring terpisah. Tugas, repositori, dan laporan adalah contoh sintetis, bukan hasil evaluasi model. Buka gambar untuk ukuran penuh.

<a href="../../docs/images/assistant/desktop-home.png"><img src="../../docs/images/assistant/desktop-home.png" alt="Beranda dan percakapan" width="1200"></a>

<table>
  <tr>
    <td width="50%" valign="top"><a href="../../docs/images/assistant/desktop-tasks.png"><img src="../../docs/images/assistant/desktop-tasks.png" alt="Buku tugas dan pemeriksaan manusia"></a><br><b>Buku tugas dan pemeriksaan manusia</b></td>
    <td width="50%" valign="top"><a href="../../docs/images/assistant/desktop-research.png"><img src="../../docs/images/assistant/desktop-research.png" alt="Kondisi riset dan laporan berversi"></a><br><b>Kondisi riset dan laporan berversi</b></td>
  </tr>
</table>

## Fitur

| Bagian | Fungsi |
| --- | --- |
| Percakapan | Model teks DeepSeek, beberapa percakapan, pencarian judul, serta tema terang dan gelap. |
| Buku tugas | Tujuan, langkah, versi rencana, catatan eksekusi, dan hasil bertahan setelah mulai ulang. |
| Rencana dan pengingat | Membuat atau mengubah pengingat dan tugas berkala, dengan catatan perubahan. |
| Dokumen | Membaca Markdown lokal dan membuat keluaran dengan referensi serta pemeriksaan isi berkas. |
| Preferensi | Preferensi yang dikonfirmasi secara eksplisit, dengan masa berlaku dan penghapusan. |
| Tindakan terkendali | Pratinjau operasi berkas, pengamatan peramban, dan tindakan Windows terbatas; mendukung jeda dan pengambilalihan manual. |
| Riset | Membandingkan kandidat dengan salinan sumber, bukti, dan laporan berversi; meninjau ulang saat kondisi berubah. |
| Persetujuan dan penggunaan | Persetujuan per tindakan, peninjauan efek yang belum pasti, dan batas biaya persisten bersama. |

## Mulai menggunakan

1. Unduh dan jalankan [pemasang Windows x64 yang belum ditandatangani](https://github.com/chengli200214-beep/continuous-research-assistant/releases/download/desktop-assistant-v1.0.5/nanoMuse-Assistant-1.0.5-win-x64.exe), atau build dari sumber di bawah. Paket mencakup Python dan Chromium.
2. Buka **Connections** dari pengaturan atau pemilih model. Pilih DeepSeek, `https://api.deepseek.com`, dan `deepseek-flash`, lalu simpan kunci sendiri di penyimpanan terenkripsi lokal.
3. Tetapkan batas harian dan per tugas pada `[assistant_usage]` di `%APPDATA%\nanoMuse Assistant\assistant.toml` lokal. Nilai awal nol. Pilih kebijakan harga yang ditinjau dan dana yang mencakup reservasi; lihat [panduan](../../docs/assistant.md#local-runtime-usage-limits). Jangan simpan kunci di sana.
4. Pilih **Runtime → Use configured model**, atau keluar dari tray lalu mulai `nanoMuse-Assistant.exe --live`. Menyimpan kunci tidak mengubah mode atau memberikan anggaran.
5. Jelaskan tujuan, tinjau rencana dan setiap tindakan, lalu pantau **Task ledger**. Jeda atau ambil alih bila diperlukan.

> Profil baru memakai respons skrip luring yang diberi label. Ini bukan model bahasa luring.

## Contoh permintaan

- Susun tiga target belajar minggu ini dan daftar evaluasinya.
- Ringkas catatan Markdown dengan referensi sumber.
- Tampilkan rencana penataan berkas dan tunggu konfirmasi saya.
- Bandingkan dua repositori menurut kriteria saya dan simpan buktinya.

## Build di Windows

Memerlukan Windows x64, Git, Node.js 22, Python 3.13, dan uv. Jalankan dari akar repositori. Skrip menyiapkan dependensi JavaScript, Chromium, dan Python.

```powershell
git clone --branch research-assistant https://github.com/chengli200214-beep/continuous-research-assistant.git
cd continuous-research-assistant
uv venv .local/assistant-desktop-build --python 3.13
uv pip sync requirements-assistant-windows.txt --python .local/assistant-desktop-build/Scripts/python.exe --require-hashes --default-index https://pypi.org/simple --no-config
uv pip install --python .local/assistant-desktop-build/Scripts/python.exe --no-deps -e . --default-index https://pypi.org/simple --no-config
uv pip install --python .local/assistant-desktop-build/Scripts/python.exe pyinstaller==6.22.3 --default-index https://pypi.org/simple --no-config
powershell -File scripts/build-assistant-desktop.ps1 -Target installer
```

Aplikasi berada di `desktop/assistant/dist/win-unpacked/`; pemasang di `desktop/assistant/dist/`. Gunakan `-Target dir` untuk direktori tanpa pemasang. Pemasang lokal belum ditandatangani. Lihat [panduan desktop](../../docs/assistant-desktop.md).

## Data dan eksekusi

- Electron membuka layanan Python lokal. Percakapan, tugas, pengingat, dan versi riset disimpan dalam berkas lokal dan SQLite.
- Konteks yang diperlukan dikirim ke penyedia model pilihan. Penyimpanan lokal tidak berarti permintaan model awan tetap berada di komputer.
- Menutup jendela mempertahankan aplikasi di tray. Keluar menghentikan layanannya. Pilih profil asisten yang sudah ada dengan `--profile="D:\AssistantData\profile"`; direktori riset lama tidak diimpor otomatis.

## Cakupan saat ini

- Cabang ini berfokus pada Windows x64, sambil mempertahankan kode ponsel, relay, dan Harness upstream.
- Profil terpisah mendukung adaptor teks DeepSeek yang ditinjau dan tindakan Windows deterministik. Dikte, kontrol model visual, dan memori embedding tidak diaktifkan.
- Pengingat memerlukan proses aktif dan komputer tidak tidur. Pemulihan arsitektur baru setelah tidur atau layar terkunci belum dinyatakan terverifikasi.

## Dokumentasi

- [Penggunaan desktop, build, dan data lama](../../docs/assistant-desktop.md)
- [Rencana, persetujuan, dan batas penggunaan](../../docs/assistant.md)
- [Arsitektur](../../docs/architecture.md)
- [Pemberitahuan upstream dan pihak ketiga](../../THIRD_PARTY_NOTICES.md)

```text
desktop/assistant/   Electron shell
web/src/             React UI
nanomuse/assistant/  Task ledger and action adapters
nanomuse/research/   Evidence and research reports
scripts/desktop-app/ Runtime packaging
```

## Lisensi dan atribusi

Berbasis nanoMuse di bawah **GPL-3.0-or-later**. Pemberitahuan tetap ada di [LICENSE](../../LICENSE), [NOTICE](../../NOTICE), dan [THIRD_PARTY_NOTICES.md](../../THIRD_PARTY_NOTICES.md). Desktop DeepSeek Harness tetap terpisah. Aturan: [CONTRIBUTING.md](../../CONTRIBUTING.md).

<details>
<summary>Kontributor komunitas upstream</summary>

<!-- contributors:start -->
<p>
<a href="https://github.com/wsvn53"><img src="https://avatars.githubusercontent.com/u/912204?v=4&s=48" width="48" height="48" alt="wsvn53"></a>
<a href="https://github.com/cutey-none"><img src="https://avatars.githubusercontent.com/u/47075002?v=4&s=48" width="48" height="48" alt="cutey-none"></a>
<a href="https://github.com/Mola-maker"><img src="https://avatars.githubusercontent.com/u/229602071?v=4&s=48" width="48" height="48" alt="Mola-maker"></a>
<a href="https://github.com/PovedaAqui"><img src="https://avatars.githubusercontent.com/u/9494679?v=4&s=48" width="48" height="48" alt="PovedaAqui"></a>
<a href="https://github.com/jiangkaiqi2005"><img src="https://avatars.githubusercontent.com/u/184961141?v=4&s=48" width="48" height="48" alt="jiangkaiqi2005"></a>
<a href="https://github.com/Ichiro786"><img src="https://avatars.githubusercontent.com/u/158778170?v=4&s=48" width="48" height="48" alt="Ichiro786"></a>
<a href="https://github.com/hobostay"><img src="https://avatars.githubusercontent.com/u/110803307?v=4&s=48" width="48" height="48" alt="hobostay"></a>
<a href="https://github.com/aliabbas6622"><img src="https://avatars.githubusercontent.com/u/193962983?v=4&s=48" width="48" height="48" alt="aliabbas6622"></a>
<a href="https://github.com/cypggs"><img src="https://avatars.githubusercontent.com/u/3694954?v=4&s=48" width="48" height="48" alt="cypggs"></a>
</p>
<!-- contributors:end -->

</details>
