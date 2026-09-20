/**
 * Shopee Cookie -> one-sheet order checker.
 *
 * Columns:
 * A Cookie | B Mã Vận Đơn | C Trạng thái Đơn | D Người nhận
 * E Số điện thoại nhận | F Địa chỉ | G Sản phẩm | H Link sản phẩm
 * I Voucher hiện có | J Địa chỉ mặc định
 *
 * Input one cookie per row in column A. Install the edit trigger once from
 * the menu so every pasted cookie row is checked automatically.
 */

var SIMPLE_HEADERS = [
  'Cookie', 'Mã Vận Đơn', 'Trạng thái Đơn', 'Người nhận',
  'Số điện thoại nhận', 'Địa chỉ', 'Sản phẩm', 'Link sản phẩm',
  'Voucher hiện có', 'Địa chỉ mặc định'
];
var SIMPLE_SHEET_NAME = 'Shopee';
var SIMPLE_TRIGGER_HANDLER = 'onEditInstalled';
var SIMPLE_ORIGIN = 'https://shopee.vn';
var SIMPLE_SPX_ORIGIN = 'https://spx.vn';
var SIMPLE_GHN_ORIGIN = 'https://fe-online-gateway.ghn.vn';
var SIMPLE_MAX_NOTIFICATIONS = 100;
var SIMPLE_MAX_VOUCHER_PAGES = 20;
var SIMPLE_VOUCHER_LIMIT = 50;

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('Shopee')
    .addItem('Tạo bảng 1 trang', 'setupSimpleSheet')
    .addItem('Bật tự động kiểm tra khi dán cookie', 'installAutoCheck')
    .addItem('Kiểm tra dòng đang chọn', 'checkSelectedRows')
    .addItem('Xóa kết quả các dòng đang chọn', 'clearSelectedRows')
    .addToUi();
}

function setupSimpleSheet() {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getActiveSheet();
  sheet.setName(SIMPLE_SHEET_NAME);
  sheet.getRange(1, 1, 1, SIMPLE_HEADERS.length).setValues([SIMPLE_HEADERS]);
  sheet.setFrozenRows(1);
  sheet.getRange(1, 1, 1, SIMPLE_HEADERS.length)
    .setFontWeight('bold')
    .setFontColor('#ffffff')
    .setBackground('#ee4d2d')
    .setVerticalAlignment('middle');
  sheet.setRowHeight(1, 32);
  var widths = [420, 190, 230, 170, 150, 330, 300, 360, 520, 430];
  for (var i = 0; i < widths.length; i++) sheet.setColumnWidth(i + 1, widths[i]);
  sheet.getRange(2, 1, Math.max(sheet.getMaxRows() - 1, 1), SIMPLE_HEADERS.length)
    .setVerticalAlignment('top')
    .setWrap(true);
  sheet.getRange('A1').setNote(
    'Dán mỗi cookie vào một dòng. Cookie là dữ liệu đăng nhập nhạy cảm; chỉ dùng Sheet riêng của bạn.'
  );
  SpreadsheetApp.getUi().alert(
    'Đã tạo bảng 1 trang. Tiếp theo chọn “Bật tự động kiểm tra khi dán cookie”.'
  );
}

function installAutoCheck() {
  var ss = SpreadsheetApp.getActive();
  var triggers = ScriptApp.getProjectTriggers();
  for (var i = 0; i < triggers.length; i++) {
    if (triggers[i].getHandlerFunction() === SIMPLE_TRIGGER_HANDLER) {
      ScriptApp.deleteTrigger(triggers[i]);
    }
  }
  ScriptApp.newTrigger(SIMPLE_TRIGGER_HANDLER).forSpreadsheet(ss).onEdit().create();
  SpreadsheetApp.getUi().alert(
    'Đã bật tự động. Từ bây giờ dán cookie vào cột A, dòng đó sẽ tự kiểm tra.'
  );
}

/** Installable on-edit trigger. */
function onEditInstalled(e) {
  if (!e || !e.range) return;
  var range = e.range;
  var sheet = range.getSheet();
  if (sheet.getName() !== SIMPLE_SHEET_NAME) return;
  if (range.getColumn() > 1 || range.getLastColumn() < 1 || range.getLastRow() < 2) return;

  var start = Math.max(2, range.getRow());
  var end = range.getLastRow();
  var rows = [];
  for (var row = start; row <= end; row++) rows.push(row);
  processRows_(sheet, rows);
}

function checkSelectedRows() {
  var sheet = SpreadsheetApp.getActiveSheet();
  var range = sheet.getActiveRange();
  if (!range) return;
  var rows = [];
  var start = Math.max(2, range.getRow());
  for (var row = start; row <= range.getLastRow(); row++) rows.push(row);
  processRows_(sheet, rows);
}

function clearSelectedRows() {
  var sheet = SpreadsheetApp.getActiveSheet();
  var range = sheet.getActiveRange();
  if (!range) return;
  var start = Math.max(2, range.getRow());
  var count = range.getLastRow() - start + 1;
  if (count > 0) sheet.getRange(start, 2, count, SIMPLE_HEADERS.length - 1).clearContent();
}

function processRows_(sheet, rows) {
  var lock = LockService.getDocumentLock();
  if (!lock.tryLock(1000)) return;
  try {
    for (var i = 0; i < rows.length; i++) processRow_(sheet, rows[i]);
  } finally {
    lock.releaseLock();
  }
}

function processRow_(sheet, row) {
  var cookie = normalizeCookie_(sheet.getRange(row, 1).getDisplayValue());
  if (!cookie) {
    sheet.getRange(row, 2, 1, SIMPLE_HEADERS.length - 1).clearContent();
    sheet.getRange(row, 3).clearNote();
    return;
  }

  var output = ['', 'Đang kiểm tra…', '', '', '', '', '', 'Đang tải voucher…',
    'Đang tải địa chỉ mặc định…'];
  sheet.getRange(row, 2, 1, output.length).setValues([output]);
  SpreadsheetApp.flush();

  try {
    var voucherResult = getAvailableVouchers_(cookie);
    var voucherText = voucherResult.text || voucherResult.error || '';
    // Read the account's default delivery address independently of order lookup.
    // This still works when order notifications/detail are unavailable.
    var defaultAddress = getDefaultAddress_(cookie);
    var defaultAddressText = formatDefaultAddress_(defaultAddress);
    var notifications = getNotifications_(cookie);
    var found = pickLatestOrder_(notifications);
    if (!found) {
      sheet.getRange(row, 2, 1, 9).setValues([[
        '', 'Không tìm thấy thông báo đơn hàng', defaultAddress.receiver || '',
        defaultAddress.phone || '', defaultAddress.address || '', '', '', voucherText,
        defaultAddressText
      ]]);
      sheet.getRange(row, 3).clearNote();
      return;
    }

    var detail = found.orderId ? getOrderDetail_(cookie, found.orderId) : null;
    var address = defaultAddress;
    var shipping = found.tracking ? getCarrierInfo_(found.tracking, found.carrier, found.status) : {};
    var itemCard = detail && detail.blocked ? getNotificationItemCard_(cookie, found.raw) : {};
    var merged = mergeOrderData_(found, detail, address, shipping, itemCard);
    if ((!merged.product || !merged.productUrl) && merged.itemId && merged.shopId) {
      var publicItem = getPublicItem_(merged.shopId, merged.itemId);
      merged = mergeOrderData_(found, detail, address, shipping, itemCard, publicItem);
    }
    var status = formatShippingStatus_(shipping, merged.status || 'Đã tìm thấy đơn');
    if (shipping && shipping.carrier && shipping.lookupUrl && shipping.requiresManualLookup) {
      status += ' | cần xác minh trên trang ' + shipping.carrier;
    }
    var notes = [];
    var shippingNote = buildShippingNote_(shipping, found.tracking);
    if (shippingNote) notes.push(shippingNote);
    if (detail && detail.blocked) {
      notes.push('Shopee chặn API chi tiết đơn với mã 90309999. Dữ liệu vận chuyển vẫn lấy từ nhà vận chuyển.');
    }

    sheet.getRange(row, 2, 1, 9).setValues([[
      merged.tracking || '',
      status,
      merged.receiver || '',
      merged.phone || '',
      merged.address || '',
      merged.product || '',
      merged.productUrl || '',
      voucherText,
      defaultAddressText
    ]]);
    if (notes.length) sheet.getRange(row, 3).setNote(notes.join('\n\n'));
    else sheet.getRange(row, 3).clearNote();
    if (!merged.product) {
      sheet.getRange(row, 7).setNote('Cookie này không trả tên sản phẩm qua thông báo; API chi tiết đơn đang bị Shopee chặn 90309999.');
    } else sheet.getRange(row, 7).clearNote();
    if (!merged.productUrl) {
      sheet.getRange(row, 8).setNote('Không có shop_id và item_id nên chưa thể tạo link sản phẩm chính xác.');
    } else sheet.getRange(row, 8).clearNote();
  } catch (err) {
    sheet.getRange(row, 2, 1, 9).setValues([[
      '', 'Lỗi: ' + safeError_(err), '', '', '', '', '', voucherText || '',
      defaultAddressText || ''
    ]]);
  }
}

function getAvailableVouchers_(cookie) {
  var cursor = '';
  var seen = {};
  var rows = [];
  var lastError = '';
  for (var page = 0; page < SIMPLE_MAX_VOUCHER_PAGES; page++) {
    var payload = {
      exclude_user_voucher_list_type: [],
      voucher_status: 1,
      voucher_sort_flag: 1,
      cursor: cursor,
      limit: SIMPLE_VOUCHER_LIMIT,
      addition: ['voucher_microsite_link'],
      version: 7,
      need_statistics: true,
      user_voucher_list_type: 1
    };
    var result = shopeePost_(cookie,
      SIMPLE_ORIGIN + '/api/v4/voucher_wallet/get_user_voucher_list',
      payload,
      SIMPLE_ORIGIN + '/user/voucher-wallet');
    var error = apiError_(result.json);
    if (!result.json || result.status < 200 || result.status >= 300 ||
        (error && String(error) !== '0')) {
      lastError = 'Không lấy được voucher' + (error ? ' (' + error + ')' : '');
      break;
    }
    var data = (result.json || {}).data || {};
    var list = data.user_voucher_list || [];
    for (var i = 0; i < list.length; i++) {
      var line = formatVoucher_(list[i]);
      var code = voucherCode_(list[i]);
      if (!code) code = line;
      if (!line || seen[code]) continue;
      seen[code] = true;
      rows.push(line);
    }
    var next = data.next || data.next_cursor || '';
    if (!next || !list.length || next === cursor) break;
    cursor = String(next);
  }
  if (rows.length) return {text: rows.join('\n'), error: ''};
  return {text: '', error: lastError || 'Không có voucher đang có'};
}

function voucherCode_(item) {
  item = item || {};
  var info = item.voucher || item.voucher_info || item;
  return String(firstValue_(info, ['voucher_code', 'code', 'voucherCode']) || '').trim();
}

function formatVoucher_(item) {
  item = item || {};
  var info = item.voucher || item.voucher_info || item;
  var code = voucherCode_(item);
  if (!code) return '';
  var title = firstValue_(info, ['title', 'icon_text', 'customised_label', 'display_shop_name']) || '';
  var shop = firstValue_(info, ['shop_name', 'customised_mall_name', 'display_shop_name']) || '';
  var rewardType = Number(firstValue_(info, ['reward_type']));
  var percentage = firstValue_(info, ['discount_percentage', 'reward_percentage']);
  var value = firstValue_(info, ['discount_value', 'reward_value']);
  var cap = firstValue_(info, ['discount_cap', 'reward_cap']);
  var minSpend = firstValue_(info, ['min_spend', 'min_amount']);
  var end = firstValue_(info, ['end_time', 'expire_time', 'expired_time']);
  var parts = [code];
  if (title && title !== code) parts.push(String(title));
  if (shop && shop !== title) parts.push(String(shop));
  if (percentage !== '' && percentage !== null && percentage !== undefined && Number(percentage) > 0) {
    parts.push(String(percentage) + '%');
  } else if (value !== '' && value !== null && value !== undefined) {
    parts.push(formatMoney_(value));
  } else if (rewardType === 2) {
    parts.push('Miễn phí vận chuyển');
  }
  if (cap !== '' && cap !== null && cap !== undefined && Number(cap)) {
    parts.push('tối đa ' + formatMoney_(cap));
  }
  if (minSpend !== '' && minSpend !== null && minSpend !== undefined && Number(minSpend)) {
    parts.push('đơn tối thiểu ' + formatMoney_(minSpend));
  }
  if (end) parts.push('hết ' + formatUnixTime_(end));
  return parts.join(' | ');
}

function formatMoney_(value) {
  var number = Number(value);
  if (!isFinite(number)) return String(value || '');
  // Shopee voucher money fields are commonly stored in VND x 100000.
  if (Math.abs(number) >= 100000) number = Math.round(number / 100000);
  return number.toLocaleString('vi-VN') + 'đ';
}

function getNotifications_(cookie) {
  var path = '/api/v4/notification/get_notifications?action_cate=4&cursor=&limit=' + SIMPLE_MAX_NOTIFICATIONS;
  var result = shopeeGet_(cookie, SIMPLE_ORIGIN + path);
  if (!result.json || result.status < 200 || result.status >= 300) {
    throw new Error('Shopee notification HTTP ' + result.status);
  }
  if (apiError_(result.json) && String(apiError_(result.json)) !== '0') {
    throw new Error('Shopee notification ' + apiError_(result.json));
  }
  return (((result.json || {}).data || {}).actions) || [];
}

function pickLatestOrder_(actions) {
  var fallback = null;
  for (var i = 0; i < actions.length; i++) {
    var action = actions[i] || {};
    var title = cleanText_(action.title);
    var content = cleanText_(action.content);
    var redirect = action.action_redirect_url || action.pc_redirect_url || '';
    var all = title + ' ' + content + ' ' + redirect;
    var carrier = detectCarrier_(all, action);
    var tracking = extractTracking_(all, carrier, action);
    var orderId = extractOrderId_(redirect) || firstValue_(action.id_info, ['orderid', 'order_id', 'orderId']);
    var orderSn = extractOrderSn_(content + ' ' + title, tracking);
    if (!(orderId || tracking || orderSn)) continue;
    var candidate = {
      orderId: String(orderId || ''),
      orderSn: String(orderSn || ''),
      tracking: String(tracking || ''),
      carrier: carrier,
      status: title || content,
      raw: action
    };
    // Prefer the newest notification that contains a real waybill. Some
    // Shopee notifications have the order id first and the carrier code later.
    if (tracking) return candidate;
    if (!fallback) fallback = candidate;
  }
  return fallback;
}

function getOrderDetail_(cookie, orderId) {
  var result = shopeeGet_(cookie, SIMPLE_ORIGIN + '/api/v4/order/get_order_detail?order_id=' + encodeURIComponent(orderId));
  var error = apiError_(result.json);
  if (String(error) === '90309999' || result.status === 403) return {blocked: true, data: {}};
  if (!result.json || error && String(error) !== '0') return {blocked: false, data: {}};
  return {blocked: false, data: (result.json || {}).data || {}};
}

function formatDefaultAddress_(address) {
  address = address || {};
  return [address.receiver, address.phone, address.address]
    .filter(function (value) { return value !== undefined && value !== null && String(value).trim() !== ''; })
    .join(' | ');
}

function getDefaultAddress_(cookie) {
  var result = shopeeGet_(cookie, SIMPLE_ORIGIN + '/api/v4/account/address/get_user_address_list');
  if (!result.json || apiError_(result.json) && String(apiError_(result.json)) !== '0') return {};
  var addresses = (((result.json || {}).data || {}).addresses) || [];
  if (!addresses.length) return {};
  var selected = addresses[0];
  for (var i = 0; i < addresses.length; i++) {
    if (addresses[i] && (addresses[i].is_delivery_address || addresses[i].is_default)) {
      selected = addresses[i];
      break;
    }
  }
  return {
    receiver: firstValue_(selected, ['name', 'receiver_name', 'recipient_name']),
    phone: firstValue_(selected, ['phone', 'phone_number', 'recipient_phone']),
    address: [selected.address, selected.district, selected.town, selected.city, selected.state, selected.country]
      .filter(function (x) { return x !== undefined && x !== null && String(x).trim() !== ''; })
      .join(', ')
  };
}

function getCarrierInfo_(tracking, carrier, fallbackStatus) {
  carrier = carrier || detectCarrier_(tracking, {});
  if (!tracking) return {status: fallbackStatus || '', carrier: carrier || ''};
  if (carrier === 'SPX') return getSpxInfo_(tracking);
  if (carrier === 'GHN') return getGhnInfo_(tracking);
  if (carrier === 'VIETTELPOST') {
    return {
      status: fallbackStatus || '',
      carrier: 'Viettel Post',
      lookupUrl: 'https://viettelpost.com.vn/tra-cuu-hanh-trinh-don/',
      requiresManualLookup: true
    };
  }
  return {status: fallbackStatus || '', carrier: carrier || ''};
}

function getSpxInfo_(tracking) {
  var url = SIMPLE_SPX_ORIGIN + '/shipment/order/open/order/get_order_info?spx_tn=' +
    encodeURIComponent(tracking) + '&language_code=vi';
  var result = shopeeGet_('', url, SIMPLE_SPX_ORIGIN + '/tracking');
  if (!result.json || String(result.json.retcode) !== '0') return {};
  var info = (((result.json || {}).data || {}).sls_tracking_info) || {};
  var records = info.records || [];
  var latest = null;
  for (var i = 0; i < records.length; i++) {
    if (!latest || Number(records[i].actual_time || 0) > Number(latest.actual_time || 0)) latest = records[i];
  }
  latest = latest || {};
  return {
    status: latest.tracking_name || latest.description || '',
    carrier: 'SPX',
    actualTime: Number(latest.actual_time || 0),
    timeline: records,
    receiver: info.receiver_name || '',
    phone: info.receiver_phone || info.phone || '',
    address: info.receiver_address || '',
    lookupUrl: SIMPLE_SPX_ORIGIN + '/vi?spx_tn=' + encodeURIComponent(tracking)
  };
}

function getGhnInfo_(tracking) {
  var url = SIMPLE_GHN_ORIGIN + '/order-tracking/public-api/client/tracking-logs';
  var result;
  try {
    result = jsonPost_(url, {order_code: tracking}, 'https://donhang.ghn.vn/');
  } catch (err) {
    return {
      status: '', carrier: 'GHN', receiver: '', phone: '', address: '',
      lookupUrl: 'https://donhang.ghn.vn/?order_code=' + encodeURIComponent(tracking),
      requiresManualLookup: true,
      errorCode: safeError_(err)
    };
  }
  var data = result.json && result.json.data;
  var latest = data && (data.latest_status || data.latest_tracking || data.current_status);
  var status = firstValueDeep_(latest, ['status_name', 'status', 'description', 'name']) ||
    firstValueDeep_(data, ['status_name', 'current_status', 'status']);
  var receiver = firstValueDeep_(data, ['receiver_name', 'to_name', 'recipient_name']);
  var phone = firstValueDeep_(data, ['receiver_phone', 'to_phone', 'recipient_phone']);
  var address = firstValueDeep_(data, ['receiver_address', 'to_address', 'recipient_address']);
  var requiresManual = !status && result.json && String(result.json.code_message || '') === 'PHONE_VERIFY_REQUIRED';
  return {
    status: status || '', carrier: 'GHN', receiver: receiver || '', phone: phone || '', address: address || '',
    lookupUrl: 'https://donhang.ghn.vn/?order_code=' + encodeURIComponent(tracking),
    requiresManualLookup: requiresManual,
    errorCode: result.json && (result.json.code_message || result.status)
  };
}

function jsonPost_(url, payload, referer) {
  var headers = {
    'User-Agent': browserUserAgent_(), 'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'vi-VN,vi;q=0.9,en;q=0.8',
    'Origin': referer ? String(referer).replace(/\/$/, '') : '', 'Referer': referer || '',
    'Content-Type': 'application/json'
  };
  var response = UrlFetchApp.fetch(url, {
    method: 'post', muteHttpExceptions: true, followRedirects: true,
    contentType: 'application/json', payload: JSON.stringify(payload), headers: headers
  });
  return {status: response.getResponseCode(), text: response.getContentText(), json: parseJson_(response.getContentText())};
}

function mergeOrderData_(found, detail, fallbackAddress, shipping, itemCard, publicItem) {
  var data = (detail && detail.data) || {};
  shipping = shipping || {};
  fallbackAddress = fallbackAddress || {};
  itemCard = itemCard || {};
  publicItem = publicItem || {};
  var rawCard = (found.raw && (found.raw.item_card_info || found.raw.item_card || found.raw.rich_contents)) || {};
  var receiver = firstValueDeep_(data, ['receiver_name', 'recipient_name', 'consignee_name', 'buyer_name', 'name']) || shipping.receiver;
  var phone = firstValueDeep_(data, ['receiver_phone', 'recipient_phone', 'consignee_phone', 'phone']) || shipping.phone;
  var address = firstValueDeep_(data, ['shipping_address', 'receiver_address', 'recipient_address', 'address_text', 'address']) || shipping.address;
  var product = firstValueDeep_(data, ['item_name', 'product_name', 'item_title', 'product_title', 'model_name']) ||
    firstValueDeep_(itemCard, ['item_name', 'product_name', 'item_title', 'product_title', 'model_name', 'name']) ||
    firstValueDeep_(rawCard, ['item_name', 'product_name', 'item_title', 'product_title', 'model_name', 'name']) ||
    firstValueDeep_(publicItem, ['item_name', 'product_name', 'item_title', 'product_title', 'name']);
  var productUrl = firstValueDeep_(data, ['product_url', 'item_url', 'share_url']) ||
    firstValueDeep_(itemCard, ['product_url', 'item_url', 'share_url']) ||
    firstValueDeep_(rawCard, ['product_url', 'item_url', 'share_url']) ||
    firstValueDeep_(publicItem, ['product_url', 'item_url', 'share_url']);
  var itemRef = extractItemReference_(data, itemCard, rawCard, found.raw || {}, publicItem);
  var itemId = itemRef.itemId;
  var shopId = itemRef.shopId;
  if (!productUrl && itemId && shopId) productUrl = 'https://shopee.vn/product/' + shopId + '/' + itemId;
  return {
    tracking: found.tracking,
    status: found.status,
    receiver: receiver || fallbackAddress.receiver || '',
    phone: phone || fallbackAddress.phone || '',
    address: address || fallbackAddress.address || '',
    product: product || '',
    productUrl: productUrl || '',
    itemId: itemId || '',
    shopId: shopId || ''
  };
}

function extractItemReference_(detailData, itemCard, rawCard, rawAction, publicItem) {
  var sources = [detailData, itemCard, rawCard, publicItem];
  for (var i = 0; i < sources.length; i++) {
    var itemId = firstValueDeep_(sources[i], ['item_id', 'itemid']);
    var shopId = firstValueDeep_(sources[i], ['shop_id', 'shopid']);
    if (itemId && shopId) return {itemId: String(itemId), shopId: String(shopId)};
  }
  var pairs = firstValueDeep_(itemCard, ['shop_item_id_list']) || firstValueDeep_(rawCard, ['shop_item_id_list']);
  if (Array.isArray(pairs) && pairs.length) {
    var pairItem = firstValue_(pairs[0], ['item_id', 'itemid']);
    var pairShop = firstValue_(pairs[0], ['shop_id', 'shopid']);
    if (pairItem && pairShop) return {itemId: String(pairItem), shopId: String(pairShop)};
  }
  var idInfo = (rawAction || {}).id_info || {};
  var actionItem = firstValue_(idInfo, ['itemid', 'item_id']);
  var actionShop = firstValue_(idInfo, ['shopid', 'shop_id']);
  // Only trust id_info when both fields are present. Some order notifications put
  // the buyer's own shop id here while itemid is null.
  if (actionItem && actionShop) return {itemId: String(actionItem), shopId: String(actionShop)};
  return {itemId: '', shopId: ''};
}

function getNotificationItemCard_(cookie, action) {
  action = action || {};
  if (action.item_card_info) return action.item_card_info;
  var actionId = action.action_id;
  if (!actionId) return {};
  var result = shopeePost_(cookie,
    SIMPLE_ORIGIN + '/api/v4/notification/get_action_content_item_card_list',
    {action_item_id_list: [String(actionId)]},
    SIMPLE_ORIGIN + '/user/notifications/order');
  if (!result.json || result.status < 200 || result.status >= 300) return {};
  if (apiError_(result.json) && String(apiError_(result.json)) !== '0') return {};
  return (result.json || {}).data || {};
}

function getPublicItem_(shopId, itemId) {
  if (!shopId || !itemId) return {};
  var url = SIMPLE_ORIGIN + '/api/v4/item/get?shopid=' + encodeURIComponent(shopId) +
    '&itemid=' + encodeURIComponent(itemId);
  var result = shopeeGet_('', url, SIMPLE_ORIGIN + '/product/' + shopId + '/' + itemId);
  if (!result.json || result.status < 200 || result.status >= 300) return {};
  return (result.json || {}).data || {};
}

function formatShippingStatus_(shipping, fallback) {
  shipping = shipping || {};
  var parts = [];
  var status = translateShippingStatus_(shipping.status || fallback || '');
  if (status) parts.push(status);
  if (shipping.carrier) parts.push(shipping.carrier);
  var timeText = formatUnixTime_(shipping.actualTime);
  if (timeText) parts.push(timeText);
  return parts.join(' | ');
}

function translateShippingStatus_(value) {
  var text = String(value || '').trim();
  var lower = text.toLowerCase();
  if (lower === 'delivered' || lower === 'delivery successful') return 'Đã giao hàng thành công';
  if (lower === 'out for delivery') return 'Đang giao hàng';
  if (lower === 'in transit') return 'Đang vận chuyển';
  if (lower === 'picked up') return 'Đã lấy hàng';
  return text;
}

function formatUnixTime_(seconds) {
  var value = Number(seconds || 0);
  if (!value) return '';
  if (value > 999999999999) value = Math.floor(value / 1000);
  try {
    return Utilities.formatDate(new Date(value * 1000), 'Asia/Ho_Chi_Minh', 'dd/MM/yyyy HH:mm');
  } catch (err) {
    return '';
  }
}

function buildShippingNote_(shipping, tracking) {
  shipping = shipping || {};
  var lines = [];
  if (shipping.carrier) lines.push('Đơn vị vận chuyển: ' + shipping.carrier);
  if (tracking) lines.push('Mã vận đơn: ' + tracking);
  if (shipping.lookupUrl) lines.push('Trang tra cứu: ' + shipping.lookupUrl);
  var records = shipping.timeline || [];
  if (records.length) {
    lines.push('Hành trình gần nhất:');
    var copy = records.slice().sort(function (a, b) {
      return Number(b.actual_time || 0) - Number(a.actual_time || 0);
    });
    for (var i = 0; i < Math.min(copy.length, 5); i++) {
      var record = copy[i] || {};
      var label = translateShippingStatus_(record.tracking_name || record.description || record.seller_description || '');
      var timestamp = formatUnixTime_(record.actual_time);
      lines.push('- ' + (timestamp ? timestamp + ': ' : '') + label);
    }
  }
  return lines.join('\n');
}

function shopeePost_(cookie, url, payload, referer) {
  var headers = {
    'User-Agent': browserUserAgent_(),
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'vi-VN,vi;q=0.9,en;q=0.8',
    'Referer': referer || SIMPLE_ORIGIN + '/',
    'Origin': SIMPLE_ORIGIN,
    'X-API-SOURCE': 'pc',
    'X-Shopee-Language': 'vi',
    'X-Requested-With': 'XMLHttpRequest',
    'Content-Type': 'application/json'
  };
  if (cookie) headers.Cookie = cookie;
  var csrf = cookieValue_(cookie, 'csrftoken');
  if (csrf) headers['x-csrftoken'] = csrf;
  var response = UrlFetchApp.fetch(url, {
    method: 'post', muteHttpExceptions: true, followRedirects: true,
    contentType: 'application/json', payload: JSON.stringify(payload || {}), headers: headers
  });
  return {status: response.getResponseCode(), text: response.getContentText(), json: parseJson_(response.getContentText())};
}

function cookieValue_(cookie, name) {
  var parts = String(cookie || '').split(';');
  for (var i = 0; i < parts.length; i++) {
    var part = parts[i].trim();
    if (part.indexOf(name + '=') === 0) return part.slice(name.length + 1);
  }
  return '';
}

function shopeeGet_(cookie, url, referer) {
  var headers = {
    'User-Agent': browserUserAgent_(),
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'vi-VN,vi;q=0.9,en;q=0.8',
    'Referer': referer || SIMPLE_ORIGIN + '/',
    'X-API-SOURCE': 'pc',
    'X-Shopee-Language': 'vi',
    'X-Requested-With': 'XMLHttpRequest'
  };
  if (cookie) headers.Cookie = cookie;
  var response = UrlFetchApp.fetch(url, {
    method: 'get', muteHttpExceptions: true, followRedirects: true, headers: headers
  });
  return {
    status: response.getResponseCode(),
    text: response.getContentText(),
    json: parseJson_(response.getContentText())
  };
}

function normalizeCookie_(value) {
  var text = String(value || '').trim();
  text = text.replace(/^Cookie\s*:\s*/i, '').trim();
  if (!text) return '';
  var parts = text.split(/[;\r\n]+/);
  var out = [];
  for (var i = 0; i < parts.length; i++) {
    var part = parts[i].trim();
    if (part && part.indexOf('=') > 0) out.push(part);
  }
  return out.join('; ');
}

function decodePossiblyHex_(value) {
  var text = String(value || '').trim();
  if (!text || text.length % 2 || !/^[0-9a-f]+$/i.test(text)) return text;
  var bytes = [];
  for (var i = 0; i < text.length; i += 2) bytes.push(parseInt(text.substr(i, 2), 16));
  try { return Utilities.newBlob(bytes).getDataAsString('UTF-8'); } catch (e) { return text; }
}

function cleanText_(value) {
  return String(decodePossiblyHex_(value) || '')
    .replace(/<br\s*\/?>/gi, ' ')
    .replace(/<[^>]*>/g, ' ')
    .replace(/&nbsp;/gi, ' ')
    .replace(/&amp;/gi, '&')
    .replace(/\s+/g, ' ')
    .trim();
}

function extractOrderId_(value) {
  var text = String(value || '');
  var patterns = [/[?&]orderId=(\d{8,25})/i, /[?&]order_id=(\d{8,25})/i, /\/order\/(\d{8,25})(?:[\/?#]|$)/i];
  for (var i = 0; i < patterns.length; i++) {
    var match = text.match(patterns[i]);
    if (match) return match[1];
  }
  return '';
}

function detectCarrier_(value, action) {
  var text = String(value || '').toLowerCase();
  var explicit = firstValueDeep_(action, ['carrier', 'carrier_name', 'shipping_provider', 'logistics_name', 'delivery_company', 'shipping_company']);
  text += ' ' + String(explicit || '').toLowerCase();
  if (/\bspx[a-z0-9]*\b|shopee\s*express/.test(text)) return 'SPX';
  if (/\bghn[a-z0-9]*\b|giao\s*hang\s*nhanh/.test(text)) return 'GHN';
  if (/\b(vtp|vtpost)[a-z0-9]*\b|viettel\s*post|viettelpost/.test(text)) return 'VIETTELPOST';
  return String(explicit || '').trim();
}

function extractTracking_(value, carrier, action) {
  var explicit = firstValueDeep_(action, [
    'tracking_number', 'tracking_no', 'tracking_code', 'waybill', 'waybill_number',
    'shipment_number', 'shipment_code', 'parcel_number', 'package_number',
    'shipping_traceno', 'shipping_tracking_number'
  ]);
  if (explicit && String(explicit).trim()) return String(explicit).trim().toUpperCase();
  var text = String(value || '').toUpperCase();
  var match = text.match(/\bSPX[A-Z0-9]{10,25}\b/);
  if (match) return match[0];
  // Only accept a code next to a tracking label to avoid mistaking order_sn/phone.
  var labelled = text.match(/(?:TRACKING|WAYBILL|MA VAN DON|VAN DON|ORDER CODE)\s*[:#-]?\s*([A-Z0-9]{6,24})/i);
  if (labelled && !/^SPC_|^HTTP|^ORDER$/.test(labelled[1])) return labelled[1];
  return '';
}

function extractOrderSn_(value, tracking) {
  var text = String(value || '').toUpperCase();
  if (tracking) text = text.replace(String(tracking).toUpperCase(), ' ');
  var matches = text.match(/\b[0-9]{6}[A-Z0-9]{6,20}\b/g) || [];
  for (var i = 0; i < matches.length; i++) if (!/^SPX/.test(matches[i])) return matches[i];
  return '';
}

function firstValue_(obj, names) {
  if (!obj || typeof obj !== 'object') return '';
  for (var i = 0; i < names.length; i++) {
    if (obj[names[i]] !== undefined && obj[names[i]] !== null && String(obj[names[i]]).trim() !== '') return obj[names[i]];
  }
  return '';
}

function firstValueDeep_(obj, names) {
  if (obj === null || obj === undefined) return '';
  if (Array.isArray(obj)) {
    for (var i = 0; i < obj.length; i++) {
      var av = firstValueDeep_(obj[i], names);
      if (av !== '') return av;
    }
    return '';
  }
  if (typeof obj !== 'object') return '';
  var direct = firstValue_(obj, names);
  if (direct !== '') return direct;
  var keys = Object.keys(obj);
  for (var j = 0; j < keys.length; j++) {
    var found = firstValueDeep_(obj[keys[j]], names);
    if (found !== '') return found;
  }
  return '';
}

function apiError_(json) {
  if (!json || typeof json !== 'object') return '';
  if (json.error !== undefined && json.error !== null) return json.error;
  if (json.error_code !== undefined && json.error_code !== null) return json.error_code;
  if (json.retcode !== undefined && json.retcode !== null) return json.retcode;
  return '';
}

function parseJson_(text) {
  try { return JSON.parse(text); } catch (e) { return null; }
}

function browserUserAgent_() {
  return 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0 Safari/537.36';
}

function safeError_(err) {
  return String((err && err.message) || err || 'không xác định')
    .replace(/SPC_ST=[^;\s]+/ig, 'SPC_ST=<redacted>')
    .slice(0, 180);
}



