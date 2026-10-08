// ControlIOS Shopee Live 1.1.1 — OCR và đồng hồ chạy trên iPhone, cần ControlIOS 4.29+.
// Tọa độ 0..1 theo khung màn hình. Bấm Dừng trên PC để ngừng.
// Mặc định mở lại Shopee. Đổi START_FROM_CURRENT thành true để dùng live đang mở.

var CONFIG = {
    SCRIPT_VERSION: "1.1.1",
    AUTO_RUN: true,
    START_FROM_CURRENT: false,
    APP_ID: "com.beeasy.shopee.vn",
    LIVE_URL: "https://live.shopee.vn/universal-link/middle-page?type=live",
    APP_START_WAIT: 20,
    COLOR_TOLERANCE: 15,
    NO_TIME_BEFORE_SWIPE: 5,
    NO_TIME_SWIPES_BEFORE_REOPEN: 5,
    OCR_NEXT_LINE_GAP: 0.018,
    OCR_MAX_MATCHES: 6
};

// Quét vùng bên phải; vị trí dòng thời gian và nút được suy ra từ nhãn thực tế.
var REGIONS = {
    REWARD_HEADER: [0.60, 0.07, 1, 0.36],
    REWARD_PANEL: [0.62, 0.10, 1, 0.46],
    CLAIM: [0.78, 0.16, 0.995, 0.43],
    CLAIM_POPUP: [0, 1 / 2, 1, 3 / 4],
    CHECKIN: [0.75, 0.16, 0.995, 0.43],
    CHECKIN_POPUP: [0.12, 0.35, 0.88, 0.83],
    TIME: [0.65, 0.13, 0.995, 0.40]
};
var CLAIM_COLORS = [15357752, 15358008, 15357751, 15292734, 16777215, 15879980];
var CHECKIN_COLORS = [15221792, 15357751, 15358008];

var STOP_SCRIPT = false;
var OPEN_URL = !CONFIG.START_FROM_CURRENT;
var noTimeCount = 0;
var swipeCount = 0;
var currentState = OPEN_URL ? "OPEN_APP" : "SCAN_TIME";
var rewardPanel = null;
var timerLine = null;
var lastTimeSample = null;
var pendingClaim = null;
var claimCandidate = null;

function message(text) {
    log(String(text));
}

function randomBetween(minimum, maximum) {
    return minimum + Math.random() * (maximum - minimum);
}

function swipeUpRandom() {
    // ControlIOS có swipe liên tục, không có touchMove từng điểm như AutoTouch.
    swipe(0.5, 0.82, randomBetween(0.46, 0.54), 0.08, 0.64);
    rewardPanel = null;
    timerLine = null;
    claimCandidate = null;
    pendingClaim = null;
    sleep(0.5);
}

function clockMillis() {
    return typeof now === "function" ? now() : Date.now();
}

function formatMMSS(seconds) {
    seconds = Math.max(0, Math.ceil(seconds));
    return Math.floor(seconds / 60) + ":" + ("0" + seconds % 60).slice(-2);
}

function countdown(seconds, sampledAt, checkClaim) {
    // Thời hạn tính từ lúc lấy ảnh OCR, không từ lúc OCR hoàn tất.
    // Mỗi lượt tính lại bằng đồng hồ iPhone; sleep/OCR chậm không cộng dồn sai số.
    var deadline = (sampledAt === undefined ? clockMillis() : sampledAt) + Math.max(0, seconds) * 1000;
    var lastLog = -1;
    var nextCheck = clockMillis();
    while (!STOP_SCRIPT) {
        var remaining = Math.max(0, (deadline - clockMillis()) / 1000);
        if (remaining <= 0) {
            // Kiểm tra lần cuối trước quyết định vuốt ở mốc time-29.
            if (checkClaim) {
                var atDeadline = cachedReadyClaim();
                if (atDeadline) pendingClaim = atDeadline;
            }
            break;
        }
        if (checkClaim && clockMillis() >= nextCheck) {
            var ready = cachedReadyClaim();
            if (ready) {
                pendingClaim = ready;
                message("Nút Nhận đã bật trước thời hạn: nhận ngay");
                return true;
            }
            nextCheck = clockMillis() + 5000;
            remaining = Math.max(0, (deadline - clockMillis()) / 1000);
            if (remaining <= 0) break;
        }
        var whole = Math.ceil(remaining);
        if (lastLog < 0 || lastLog - whole >= 5 || whole <= 5 && lastLog !== whole) {
            message("WAIT: " + whole + "s (" + formatMMSS(whole) + ")");
            lastLog = whole;
        }
        sleep(Math.min(1, remaining));
    }
    return !STOP_SCRIPT;
}

function colorHex(number) {
    return ("000000" + (number >>> 0).toString(16).toUpperCase()).slice(-6);
}

function acceptedColor(point, colors) {
    var actual = String(getColor(point.x, point.y) || "").replace(/^#/, "").toUpperCase();
    if (!/^[0-9A-F]{6}$/.test(actual)) return false;
    message("COLOR: #" + actual);
    var tolerance = Math.max(0, CONFIG.COLOR_TOLERANCE);
    for (var i = 0; i < colors.length; i++) {
        var expected = colorHex(colors[i]);
        var matched = true;
        for (var offset = 0; offset < 6; offset += 2) {
            if (Math.abs(parseInt(actual.substr(offset, 2), 16) -
                         parseInt(expected.substr(offset, 2), 16)) > tolerance) {
                matched = false;
                break;
            }
        }
        if (matched) return true;
    }
    return false;
}

function findBottomText(words, region, firstAlias) {
    // findText trả một tâm chữ, không trả danh sách rectangle như at.ocr.
    // Tìm tiếp phía dưới kết quả để lấy nút thấp nhất; số lượt luôn có giới hạn.
    var best = null;
    for (var i = 0; i < words.length; i++) {
        var fromY = region[1];
        for (var count = 0; count < CONFIG.OCR_MAX_MATCHES && fromY < region[3]; count++) {
            var point = findText(words[i], region[0], fromY, region[2], region[3]);
            if (!point || !isFinite(point.x) || !isFinite(point.y) ||
                point.x < region[0] || point.x > region[2] ||
                point.y < fromY || point.y > region[3]) break;
            if (!best || point.y > best.y) {
                best = {x: point.x, y: point.y, text: words[i]};
            }
            var nextY = point.y + CONFIG.OCR_NEXT_LINE_GAP;
            if (nextY <= fromY) break;
            fromY = nextY;
        }
        // Các alias là cách OCR đọc cùng một chữ. Khi đã tìm được, không quét
        // lại cả bảng theo mọi cách viết nữa; vẫn tìm đủ các dòng của alias đó.
        if (best && (firstAlias === true || firstAlias === "ready" && readyButton(best, true))) break;
    }
    return best;
}

function clickBottomClaim() {
    if (!locateRewardPanel()) return null;
    var r = claimRegion();
    var p = findBottomText(["nhân", "nhận", "nhan", "nhin", "claim", "aim"], r, "ready");
    if (p) {
        p.foundAt = clockMillis();
        claimCandidate = p;
    }
    return p;
}

function cachedReadyClaim() {
    var p = claimCandidate;
    if (!p || !readyButton(p, true)) return null;
    // Kiểm tra lại CHỮ trong vùng nhỏ trước khi dùng vị trí cũ, không chỉ màu.
    var fresh = findText(p.text, Math.max(0.78,p.x-0.07), Math.max(0,p.y-0.015),
                         Math.min(0.995,p.x+0.07), Math.min(0.46,p.y+0.015));
    if (!fresh || Math.abs(fresh.x-p.x)>0.04 || Math.abs(fresh.y-p.y)>0.015 || !readyButton(fresh)) return null;
    fresh.text = p.text;
    fresh.foundAt = clockMillis();
    claimCandidate = fresh;
    return fresh;
}

function clickButtonClaim2() {
    // Hàm có trong bản gốc, hiện chưa được luồng chính gọi.
    return findBottomText(["claim", "nhận", "nhan"], REGIONS.CLAIM_POPUP);
}

function clickButtonCheckin1() {
    var panel = locateRewardPanel();
    if (!panel) return null;
    var r = panel ? [0.75, panel.y + 0.015, 0.995, Math.min(0.46, panel.y + 0.20)] : REGIONS.CHECKIN;
    return findBottomText(["điểm", "diem", "dim", "check"], r);
}

function clickButtonCheckin2() {
    var point = findBottomText(["danh", "check"], REGIONS.CHECKIN_POPUP);
    return point && popupWhiteCard(point) && readyButton(point) ? point : null;
}

function locateRewardPanel() {
    if (rewardPanel) return rewardPanel;
    var words = ["phan", "thuong", "thugng", "thưởng", "phần", "phän", "reward"];
    var r = REGIONS.REWARD_HEADER;
    for (var i = 0; i < words.length; i++) {
        var p = findText(words[i], r[0], r[1], r[2], r[3]);
        if (p) {
            rewardPanel = {x:p.x, y:p.y};
            message("Bảng Phần thưởng: " + p.x.toFixed(3) + "," + p.y.toFixed(3));
            return rewardPanel;
        }
    }
    return null;
}

function timerRegion() {
    if (timerLine) return timerLine;
    var panel = locateRewardPanel();
    var r = panel ? [0.63, panel.y + 0.010, 0.995, Math.min(0.46, panel.y + 0.20)] : REGIONS.TIME;
    var caption = findBottomText(["xem", "watch"], r, true);
    // Tách đúng dòng có bộ đếm, tránh cắt mất hai số đầu hoặc nhầm số Xu.
    if (caption) timerLine = [0.63, Math.max(0.08, caption.y - 0.016), 0.995, caption.y + 0.016];
    return timerLine || r;
}

function claimRegion() {
    var panel = locateRewardPanel();
    if (!panel) return REGIONS.CLAIM;
    // Không tìm dòng Xem trước mỗi lần tìm Nhận: khi thưởng sẵn sàng,
    // dòng Xem dưới đã đổi thành Nhấn để nhận thưởng.
    return [0.78, panel.y + 0.025,
            0.995, Math.min(0.46, panel.y + 0.20)];
}

function rgb(hex) {
    hex = String(hex || "").replace(/^#/, "");
    if (!/^[0-9a-f]{6}$/i.test(hex)) return null;
    return [parseInt(hex.substr(0,2),16), parseInt(hex.substr(2,2),16), parseInt(hex.substr(4,2),16)];
}

function warmButtonColor(hex) {
    var c = rgb(hex);
    // Nút đang bật là đỏ/cam sáng; nền xám và đỏ tối của nút disabled không đạt.
    return !!c && c[0] >= 215 && c[1] >= 25 && c[1] <= 185 && c[2] <= 130 && c[0] - c[1] >= 45;
}

function readyButton(p, quiet) {
    var probes = [[-0.030,0.008],[0.030,0.008],[0,0.011],[-0.022,-0.008],[0.022,-0.008]];
    var hits = 0;
    for (var i = 0; i < probes.length; i++) {
        var x = Math.max(0, Math.min(1, p.x + probes[i][0]));
        var y = Math.max(0, Math.min(1, p.y + probes[i][1]));
        if (warmButtonColor(getColor(x,y))) hits++;
    }
    if (!quiet) message("Màu nền nút: " + hits + "/5 điểm đang bật");
    return hits >= 2;
}

function popupWhiteCard(p) {
    var hits = 0;
    var points = [[-0.18,-0.035],[0.18,-0.035],[-0.18,0.035],[0.18,0.035]];
    for (var i = 0; i < points.length; i++) {
        var c = rgb(getColor(Math.max(0,Math.min(1,p.x+points[i][0])),
                             Math.max(0,Math.min(1,p.y+points[i][1]))));
        if (c && c[0] >= 235 && c[1] >= 235 && c[2] >= 235) hits++;
    }
    return hits >= 2;
}

function parseTimeMMSS(text) {
    var pattern = /(^|[^0-9])(\d{1,2})\s*[:.;\/]\s*(\d{2})(?!\d)/g;
    var match;
    var maximum = null;
    text = String(text || "").replace(/\uFF1A/g, ":");
    // OCR nhỏ có thể làm mất dấu phân cách. Chỉ nhận 00 SS, tránh nhầm số Xu.
    text = text.replace(/(^|[^0-9])(00)\s+(\d{2})(?!\d)/g, "$1$2:$3");
    while ((match = pattern.exec(text)) !== null) {
        var minutes = parseInt(match[2], 10);
        var seconds = parseInt(match[3], 10);
        if (seconds > 59) continue;
        var total = minutes * 60 + seconds;
        if (maximum === null || total > maximum) maximum = total;
    }
    return maximum;
}

function readTimeMMSS() {
    var r = timerRegion();
    var sampledAt = clockMillis();
    var text = ocr(r[0], r[1], r[2], r[3]);
    message("TIME OCR: " + String(text || "").replace(/\s+/g, " ").trim());
    var seconds = parseTimeMMSS(text);
    lastTimeSample = seconds === null ? null : {seconds: seconds, sampledAt: sampledAt};
    if (seconds === null) timerLine = null;
    message(seconds === null ? "NO MM:SS" : "TIME: " + formatMMSS(seconds) + " = " + seconds + "s; OCR " +
            ((clockMillis()-sampledAt)/1000).toFixed(1) + "s");
    return seconds;
}

function resetState() {
    noTimeCount = 0;
    swipeCount = 0;
}

function handleNoTime() {
    noTimeCount++;
    message("NO TIME: " + noTimeCount);
    if (noTimeCount >= CONFIG.NO_TIME_BEFORE_SWIPE) {
        swipeCount++;
        noTimeCount = 0; // Không reset swipeCount trước khi kiểm tra đủ 5 lần.
        message("NO-TIME SWIPE: " + swipeCount);
        swipeUpRandom();
        if (swipeCount >= CONFIG.NO_TIME_SWIPES_BEFORE_REOPEN) {
            message("Không đọc được time sau 5 lượt vuốt: mở lại Shopee");
            resetState();
            OPEN_URL = true;
            currentState = "OPEN_APP";
            return;
        }
    }
    sleep(5);
    currentState = "SCAN_TIME";
}

function scanTimeStep1() {
    // Nhận phần thưởng đã sẵn sàng trước khi đọc bộ đếm cũ/đã về 00:00.
    var pending = clickBottomClaim();
    if (pending && readyButton(pending)) {
        pendingClaim = pending;
        currentState = "CLAIM";
        return;
    }
    sleep(3);
    var time1 = readTimeMMSS();
    if (time1 === null) { handleNoTime(); return; }
    sleep(2);
    var time2 = readTimeMMSS();
    if (time2 === null) { handleNoTime(); return; }
    message("TIME1=" + time1 + " | TIME2=" + time2);
    var sampledAt = lastTimeSample ? lastTimeSample.sampledAt : clockMillis();
    var newlyReady = cachedReadyClaim();
    if (newlyReady) {
        pendingClaim = newlyReady;
        currentState = "CLAIM";
        return;
    }

    if (time1 === time2 || time2 > 600) {
        message(time1 === time2 ? "Time không đổi: vuốt" : "Time > 600s: vuốt");
        swipeUpRandom();
        sleep(2);
        currentState = "SCAN_TIME";
    } else if (time2 < 30 || time2 > 60) {
        if (countdown(time2, sampledAt, true)) currentState = "CLAIM";
    } else {
        // Giữ công thức gốc time2 - 29: 30..60s thì chờ 1..31s rồi vuốt.
        var waitSeconds = time2 - 29;
        message("Chờ " + waitSeconds + "s để còn khoảng 29s rồi vuốt");
        if (countdown(waitSeconds, sampledAt, true)) {
            if (pendingClaim) { currentState = "CLAIM"; return; }
            swipeUpRandom();
            sleep(2);
            currentState = "SCAN_TIME";
        }
    }
}

function tryClaim() {
    message("FIND CLAIM");
    // Kết quả vừa tìm được dùng ngay, tránh quét toàn bảng lần thứ hai.
    var claim = pendingClaim;
    pendingClaim = null;
    if (!claim || clockMillis() - claim.foundAt > 2000) claim = cachedReadyClaim() || clickBottomClaim();
    if (!claim) {
        message("NO CLAIM: quét time tiếp");
        currentState = "SCAN_TIME";
        return;
    }
    if (readyButton(claim)) {
        tap(claim.x, claim.y);
        message("Đã bấm CLAIM; chưa xác nhận nhận thưởng");
        timerLine = null;
        claimCandidate = null;
    } else {
        message("WRONG COLOR: không bấm CLAIM");
    }
    resetState();
    sleep(5);
    currentState = "SCAN_TIME";
}

function finishOpenApp() {
    OPEN_URL = false;
    currentState = "SCAN_TIME";
}

function openAppAndCheckin() {
    rewardPanel = null;
    timerLine = null;
    claimCandidate = null;
    pendingClaim = null;
    killApp(CONFIG.APP_ID);
    sleep(2);
    openURL(CONFIG.LIVE_URL);
    message("Đợi Shopee khởi động " + CONFIG.APP_START_WAIT + "s");
    if (!countdown(CONFIG.APP_START_WAIT)) return;

    var first = clickButtonCheckin1();
    if (!first) {
        message("NO CHECK-IN 1");
        finishOpenApp();
        return;
    }
    if (!readyButton(first)) {
        message("WRONG COLOR CHECK-IN 1");
        finishOpenApp();
        return;
    }
    tap(first.x, first.y);
    message("Đã bấm CHECK-IN 1");
    sleep(5);
    var second = clickButtonCheckin2();
    if (second) {
        tap(second.x, second.y);
        message("Đã bấm CHECK-IN 2");
    } else {
        message("NO CHECK-IN 2");
    }
    finishOpenApp();
}

function mainLoop() {
    message("SCRIPT STARTED - ControlIOS");
    while (!STOP_SCRIPT) {
        switch (currentState) {
            case "IDLE": currentState = OPEN_URL ? "OPEN_APP" : "SCAN_TIME"; break;
            case "OPEN_APP": openAppAndCheckin(); break;
            case "SCAN_TIME": scanTimeStep1(); break;
            case "CLAIM": tryClaim(); break;
            default: currentState = "IDLE"; break;
        }
        sleep(0.6);
    }
}

if (CONFIG.AUTO_RUN) mainLoop();
