# nanoMuse Assistant

Trợ lý cá nhân Windows với sổ tác vụ và mô-đun nghiên cứu có bằng chứng.

[English](../../README.md) | [简体中文](../../docs/readme/README_zh.md) | [繁體中文](../../docs/readme/README_zh-TW.md) | [Español](../../docs/readme/README_es.md) | [Français](../../docs/readme/README_fr.md) | [Bahasa Indonesia](../../docs/readme/README_id.md) | [日本語](../../docs/readme/README_ja.md) | [한국어](../../docs/readme/README_ko.md) | [Русский](../../docs/readme/README_ru.md) | [Tiếng Việt](../../docs/readme/README_vi.md)

<p>
  <img src="https://img.shields.io/badge/Windows-x64-5d7161" alt="Windows x64">
  <img src="https://img.shields.io/badge/DeepSeek-text-c47759" alt="DeepSeek text">
  <img src="https://img.shields.io/badge/UI-React%20%2B%20TypeScript-6e7f91" alt="React and TypeScript">
  <a href="../../LICENSE"><img src="https://img.shields.io/badge/license-GPL--3.0--or--later-746b62" alt="GPL-3.0-or-later"></a>
  <br>
  <a href="../../docs/assistant-desktop.md"><img src="https://img.shields.io/badge/Docs-desktop-746b62" alt="Desktop documentation"></a>
  <a href="../../docs/assistant.md"><img src="https://img.shields.io/badge/Docs-task%20ledger-746b62" alt="Task ledger documentation"></a>
</p>

**nanoMuse Assistant** kết hợp hội thoại, kế hoạch, lời nhắc, tài liệu và nghiên cứu. Nêu mục tiêu, xem lại các bước rồi theo dõi kết quả trong sổ tác vụ bền vững. Bản phát triển từ [nanoMuse](https://github.com/nano-muse/nanoMuse) này bổ sung không gian Windows độc lập và giữ lại nghiên cứu.

## Giao diện sản phẩm

Ảnh bản xem trước giao diện hiện tại bằng hồ sơ ngoại tuyến riêng. Tác vụ, kho mã và báo cáo là ví dụ tổng hợp, không phải đánh giá mô hình. Mở ảnh để xem kích thước đầy đủ.

<a href="../../docs/images/assistant/desktop-home.png"><img src="../../docs/images/assistant/desktop-home.png" alt="Trang chủ và hội thoại" width="1200"></a>

<table>
  <tr>
    <td width="50%" valign="top"><a href="../../docs/images/assistant/desktop-tasks.png"><img src="../../docs/images/assistant/desktop-tasks.png" alt="Sổ tác vụ và xác nhận của người dùng"></a><br><b>Sổ tác vụ và xác nhận của người dùng</b></td>
    <td width="50%" valign="top"><a href="../../docs/images/assistant/desktop-research.png"><img src="../../docs/images/assistant/desktop-research.png" alt="Điều kiện nghiên cứu và báo cáo theo phiên bản"></a><br><b>Điều kiện nghiên cứu và báo cáo theo phiên bản</b></td>
  </tr>
</table>

## Tính năng

| Mục | Chức năng |
| --- | --- |
| Hội thoại | Mô hình văn bản DeepSeek, nhiều hội thoại, tìm theo tiêu đề, giao diện sáng và tối. |
| Sổ tác vụ | Lưu mục tiêu, bước, phiên bản kế hoạch, lịch sử và kết quả qua lần khởi động lại. |
| Kế hoạch và lời nhắc | Tạo hoặc sửa lời nhắc, tác vụ định kỳ và ghi lại thay đổi. |
| Tài liệu | Đọc Markdown cục bộ, tạo sản phẩm có nguồn và kiểm tra nội dung tệp. |
| Sở thích cá nhân | Lưu sở thích đã xác nhận rõ ràng, có thời hạn và khả năng xóa. |
| Thao tác có kiểm soát | Xem trước thao tác tệp, quan sát trình duyệt và thao tác Windows giới hạn, có tạm dừng và tiếp quản thủ công. |
| Nghiên cứu | So sánh bằng ảnh chụp nguồn, bằng chứng, phiên bản báo cáo và xem lại khi điều kiện thay đổi. |
| Phê duyệt và sử dụng | Phê duyệt từng thao tác, giữ kết quả chưa rõ để xem xét và chia sẻ giới hạn chi phí bền vững. |

## Bắt đầu

1. Biên dịch bên dưới rồi mở `nanoMuse-Assistant.exe` hoặc bộ cài. Gói ứng dụng gồm Python và Chromium.
2. Mở **Connections** từ cài đặt hoặc bộ chọn mô hình. Chọn DeepSeek, `https://api.deepseek.com`, `deepseek-flash` và lưu khóa riêng vào kho mã hóa cục bộ.
3. Đặt hạn mức ngày và tác vụ trong `[assistant_usage]` của `%APPDATA%\nanoMuse Assistant\assistant.toml`. Giá trị ban đầu là 0. Chọn chính sách giá đã xem xét và hạn mức đủ dự phòng: [hướng dẫn](../../docs/assistant.md#local-runtime-usage-limits). Không đặt khóa ở tệp này.
4. Chọn **Runtime → Use configured model**, hoặc thoát từ khay rồi chạy `nanoMuse-Assistant.exe --live`. Lưu khóa không tự đổi chế độ hay cấp ngân sách.
5. Nêu mục tiêu, xem lại kế hoạch và từng thao tác, theo dõi **Task ledger**. Có thể tạm dừng hoặc tiếp quản khi cần.

> Hồ sơ mới dùng phản hồi theo kịch bản ngoại tuyến có nhãn, không phải mô hình ngôn ngữ ngoại tuyến.

## Ví dụ yêu cầu

- Sắp xếp ba mục tiêu học tập tuần này và danh sách tổng kết.
- Tóm tắt ghi chú Markdown kèm nguồn.
- Xem trước kế hoạch sắp xếp tệp và chờ tôi xác nhận.
- So sánh hai kho mã theo yêu cầu và giữ lại bằng chứng.

## Biên dịch trên Windows

Cần Windows x64, Git, Node.js 22, Python 3.13 và uv. Chạy từ thư mục gốc. Tập lệnh chuẩn bị phụ thuộc JavaScript, Chromium và Python.

```powershell
git clone --branch research-assistant https://github.com/chengli200214-beep/continuous-research-assistant.git
cd continuous-research-assistant
uv venv .local/assistant-desktop-build --python 3.13
uv pip sync requirements-assistant-windows.txt --python .local/assistant-desktop-build/Scripts/python.exe --require-hashes --default-index https://pypi.org/simple --no-config
uv pip install --python .local/assistant-desktop-build/Scripts/python.exe --no-deps -e . --default-index https://pypi.org/simple --no-config
uv pip install --python .local/assistant-desktop-build/Scripts/python.exe pyinstaller==6.22.3 --default-index https://pypi.org/simple --no-config
powershell -File scripts/build-assistant-desktop.ps1 -Target installer
```

Ứng dụng ở `desktop/assistant/dist/win-unpacked/`, bộ cài ở `desktop/assistant/dist/`. `-Target dir` chỉ tạo bản giải nén. Bộ cài cục bộ chưa được ký. Xem [hướng dẫn desktop](../../docs/assistant-desktop.md).

## Dữ liệu và thực thi

- Electron mở dịch vụ Python cục bộ. Hội thoại, tác vụ, lời nhắc và phiên bản nghiên cứu nằm trong tệp cục bộ và SQLite.
- Ngữ cảnh cần thiết được gửi cho nhà cung cấp mô hình. Lưu cục bộ không có nghĩa yêu cầu đám mây ở lại máy tính.
- Đóng cửa sổ giữ ứng dụng trong khay. Thoát sẽ dừng dịch vụ. Chọn hồ sơ cũ bằng `--profile="D:\AssistantData\profile"`; không tự nhập thư mục chỉ dành cho nghiên cứu cũ.

## Phạm vi hiện tại

- Nhánh tập trung vào Windows x64 độc lập và giữ mã di động, relay, Harness từ upstream.
- Hồ sơ riêng hỗ trợ bộ thích ứng văn bản DeepSeek đã xem xét và thao tác Windows xác định. Chưa bật đọc chính tả, điều khiển bằng mô hình thị giác hay bộ nhớ embedding.
- Lời nhắc cần tiến trình hoạt động và máy tính không ngủ. Phục hồi kiến trúc mới sau ngủ hoặc khóa màn hình chưa được công bố là đã xác minh.

## Tài liệu

- [Sử dụng desktop, biên dịch và dữ liệu cũ](../../docs/assistant-desktop.md)
- [Kế hoạch, phê duyệt và hạn mức](../../docs/assistant.md)
- [Kiến trúc](../../docs/architecture.md)
- [Thông báo upstream và bên thứ ba](../../THIRD_PARTY_NOTICES.md)

```text
desktop/assistant/   Electron shell
web/src/             React UI
nanomuse/assistant/  Task ledger and action adapters
nanomuse/research/   Evidence and research reports
scripts/desktop-app/ Runtime packaging
```

## Giấy phép và ghi nhận

Dựa trên nanoMuse, theo **GPL-3.0-or-later**. Thông báo giữ tại [LICENSE](../../LICENSE), [NOTICE](../../NOTICE), [THIRD_PARTY_NOTICES.md](../../THIRD_PARTY_NOTICES.md). Desktop DeepSeek Harness có lối vào riêng. Quy tắc: [CONTRIBUTING.md](../../CONTRIBUTING.md).

<details>
<summary>Cộng tác viên cộng đồng upstream</summary>

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
