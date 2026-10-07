# Crawl Data From Báo Mới và VnExpress

Bản fork cá nhân: https://github.com/TrinhThanh-25/Crawl-Data-From-Baomoi

Thu thập bài trong **24 giờ trước thời điểm bắt đầu chạy**, loại trùng theo ID, lưu JSON UTF-8. Các file kết quả và môi trường chạy nằm trong thư mục project, được bỏ qua trong Git.

## Cài đặt trên Windows

Yêu cầu Python 3.9+ và kết nối mạng.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path (Get-Location) '.browsers'
.\.venv\Scripts\python.exe -m playwright install firefox
```

## Chạy cả hai crawler

```powershell
.\run_crawlers.ps1
```

Mỗi lần chạy tạo một thư mục `results_yyyy-MM-dd_HHmmss` mới trong project, chứa JSON và log của hai nguồn. Mỗi crawler dùng tối đa 3 worker, giới hạn thời gian mặc định 900 giây. Script chạy lần lượt hai nguồn; bên trong từng crawler, các worker xử lý song song.

```powershell
.\run_crawlers.ps1 -Workers 4 -MaxRuntime 900
```

## Báo Mới

```powershell
.\.venv\Scripts\python.exe -X utf8 crawl_baomoi.py --workers 3 --output results_example/baomoi_24h.json
```

Mặc định dùng HTTP đọc JSON được render sẵn trong HTML, không cần mở Firefox hoặc tải ảnh, font, quảng cáo của giao diện. Các worker dùng chung phiên HTTP; URL phân trang chỉ lấy từ liên kết thực tế của website. Khi gặp lỗi mạng hoặc HTTP 429/5xx, thử lại tối đa 3 lần có chờ. Không thay đổi chữ ký API hoặc tự đoán URL phân trang.

Đã đối chiếu ID bài từ các response API cuộn của trang đầu với HTML của trang 5: các ID quan sát được đều có trong dữ liệu HTML. Đây là bằng chứng cho cách đọc nhanh tại thời điểm kiểm tra, không chứng minh toàn bộ website luôn có cấu trúc đó.

Có thể dùng chế độ trình duyệt để đọc cả response API khi cần kiểm tra cấu trúc mới:

```powershell
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path (Get-Location) '.browsers'
.\.venv\Scripts\python.exe -X utf8 crawl_baomoi.py --engine browser --output results_example/baomoi_browser.json
```

Tùy chọn: `--hours 24`, `--max-runtime 900`, `--max-pages 1000`, `--headed` (cho engine browser). Lưu checkpoint sau mỗi trang, ghi file tạm rồi thay thế file kết quả. Kết quả có `engine`, `workers`, `elapsed_seconds`, `visited_pages`, `page_failures`, `stop_reason`, `article_count` và `articles`.

## VnExpress

```powershell
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path (Get-Location) '.browsers'
.\.venv\Scripts\python.exe -X utf8 crawl_vnexpress.py --workers 3 --output results_example/vnexpress_24h.json
```

Mỗi thread sở hữu một phiên Playwright/Firefox riêng và tái sử dụng cho nhiều bài; không chia sẻ đối tượng Playwright giữa thread. Chặn ảnh, media, font; xử lý response bình luận ngay khi nhận; giảm thời gian nghỉ giữa request API từ 700ms xuống 150ms; cache User-Agent; đếm trả lời bằng chỉ mục theo parent thay vì quét lại toàn bộ danh sách. Điều phối lưu checkpoint mỗi 5 giây và khi kết thúc.

Tùy chọn: `--comment-timeout 10`, `--request-delay 0.15`, `--max-articles 0` (mọi bài phát hiện), `--max-runtime 900`, `--headed`. `--headless` vẫn được chấp nhận và cũng là mặc định. Thời hạn chạy kiểm tra giữa các bài và request bình luận; request điều hướng đang chạy có thể kéo dài đến timeout riêng.

Giữ số bình luận đã biết ngay cả khi tải các trang tiếp theo thất bại. Điểm tương tác đầy đủ = tổng lượt thích trên các bình luận gốc/trả lời duy nhất + số trả lời duy nhất. Chỉ bài có đủ bình luận mới được đưa vào `ranking_by_interaction`; dữ liệu thiếu có điểm tạm riêng và cờ `comments_complete=false`.

## Phạm vi và kiểm tra

`completeness_verified=false` của Báo Mới luôn được giữ: đi hết liên kết phân trang quan sát được không phải đối chiếu độc lập toàn bộ tin 24 giờ. `page_limit`, `runtime_limit`, `page_failures` cho biết giới hạn hoặc trang bị thiếu. Khi mọi bài trong một trang HTML đều cũ hơn cửa sổ, crawler không mở thêm liên kết từ trang đó; một bài cũ riêng lẻ không làm dừng.

VnExpress chỉ bao gồm danh sách `tin-tuc-24h` phát hiện được, không khẳng định toàn bộ website. `ranking_is_partial` phản ánh lỗi, bài chưa xử lý, thời gian không rõ, phân trang danh sách chưa hết hoặc bình luận chưa đủ. Không suy ra số bình luận bằng 0 khi chưa nhận được dữ liệu.

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest -v
```

Kiểm thử cửa sổ thời gian, loại trùng, URL nguồn, JSON nhúng, liên kết phân trang, file UTF-8/checkpoint và xếp hạng có dữ liệu thiếu. Kết quả chạy ngày 07/10/2026 nằm ở `results_2026-10-07/` trên máy này; các dữ liệu crawl không được đẩy lên GitHub.
