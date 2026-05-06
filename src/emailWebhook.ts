import fs from "fs";
import path from "path";
import { Request, Response } from "express";
import { sendReply, getThreadMessages, formatEmailBody, ThreadMessage } from "./unipileClient";

interface Attendee {
  display_name?: string;
  identifier: string;
}

interface Attachment {
  id: string;
  filename: string;
  mime_type: string;
}

interface UnipileEmailPayload {
  event: string;
  account_id: string;
  email_id: string;
  message_id: string;
  provider_id?: string;
  thread_id?: string;
  date: string;
  from_attendee: Attendee;
  to_attendees: Attendee[];
  cc_attendees?: Attendee[];
  subject: string;
  body?: string;
  body_plain?: string;
  has_attachments?: boolean;
  attachments?: Attachment[];
  in_reply_to?: { message_id: string };
}

type LogLevel = "info" | "warn" | "error";

const IDEMPOTENCY_TTL_MS = 10 * 60 * 1000;
const IDEMPOTENCY_MAX_ENTRIES = 1000;
const STATIONW_ENDPOINT = "http://127.0.0.1:8000/parse-order";
const STATIONW_TIMEOUT_MS = 300000;

const processedMessages = new Map<string, number>();

function log(level: LogLevel, message: string, data: Record<string, unknown> = {}) {
  const payload = {
    timestamp: new Date().toISOString(),
    level,
    message,
    ...data,
  };
  const serialized = JSON.stringify(payload);
  if (level === "error") {
    console.error(serialized);
  } else if (level === "warn") {
    console.warn(serialized);
  } else {
    console.log(serialized);
  }
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function validatePayload(payload: unknown): { errors: string[]; value?: UnipileEmailPayload } {
  if (!isRecord(payload)) {
    return { errors: ["payload must be an object"] };
  }

  const errors: string[] = [];

  if (!isNonEmptyString(payload.event)) errors.push("event is required");
  if (!isNonEmptyString(payload.account_id)) errors.push("account_id is required");
  if (!isNonEmptyString(payload.email_id)) errors.push("email_id is required");
  if (!isNonEmptyString(payload.date)) errors.push("date is required");
  if (!isNonEmptyString(payload.subject)) errors.push("subject is required");

  if (!isRecord(payload.from_attendee) || !isNonEmptyString(payload.from_attendee.identifier)) {
    errors.push("from_attendee.identifier is required");
  }

  if (!Array.isArray(payload.to_attendees) || payload.to_attendees.length === 0) {
    errors.push("to_attendees must be a non-empty array");
  } else {
    payload.to_attendees.forEach((attendee, index) => {
      if (!isRecord(attendee) || !isNonEmptyString(attendee.identifier)) {
        errors.push(`to_attendees[${index}].identifier is required`);
      }
    });
  }

  if (payload.cc_attendees !== undefined) {
    if (!Array.isArray(payload.cc_attendees)) {
      errors.push("cc_attendees must be an array when provided");
    } else {
      payload.cc_attendees.forEach((attendee, index) => {
        if (!isRecord(attendee) || !isNonEmptyString(attendee.identifier)) {
          errors.push(`cc_attendees[${index}].identifier is required`);
        }
      });
    }
  }

  if (payload.attachments !== undefined) {
    if (!Array.isArray(payload.attachments)) {
      errors.push("attachments must be an array when provided");
    } else {
      payload.attachments.forEach((attachment, index) => {
        if (
          !isRecord(attachment) ||
          !isNonEmptyString(attachment.id) ||
          !isNonEmptyString(attachment.filename) ||
          !isNonEmptyString(attachment.mime_type)
        ) {
          errors.push(`attachments[${index}] must include id, filename, mime_type`);
        }
      });
    }
  }

  if (errors.length > 0) {
    return { errors };
  }

  return { errors, value: payload as unknown as UnipileEmailPayload };
}

function pruneProcessed(now: number) {
  for (const [key, timestamp] of processedMessages) {
    if (now - timestamp > IDEMPOTENCY_TTL_MS) {
      processedMessages.delete(key);
    }
  }

  while (processedMessages.size > IDEMPOTENCY_MAX_ENTRIES) {
    const oldestKey = processedMessages.keys().next().value as string;
    processedMessages.delete(oldestKey);
  }
}

function isDuplicateMessage(key: string): boolean {
  const now = Date.now();
  pruneProcessed(now);
  if (processedMessages.has(key)) {
    return true;
  }
  processedMessages.set(key, now);
  return false;
}

function delay(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function mapAttendee(a: Attendee) {
  return { name: a.display_name ?? "", email: a.identifier };
}

function normalize(payload: UnipileEmailPayload) {
  return {
    event: payload.event,
    source: "unipile",
    account_id: payload.account_id,
    email_id: payload.email_id,
    message_id: payload.message_id,
    provider_id: payload.provider_id ?? null,
    thread_id: payload.thread_id ?? null,
    mailbox: payload.to_attendees[0]?.identifier ?? "",
    received_at: payload.date,
    from: mapAttendee(payload.from_attendee),
    to: (payload.to_attendees ?? []).map(mapAttendee),
    cc: (payload.cc_attendees ?? []).map(mapAttendee),
    subject: payload.subject,
    body_text: payload.body_plain ?? "",
    body_html: payload.body ?? "",
    has_attachments: payload.has_attachments ?? false,
    attachments: (payload.attachments ?? []).map((a) => ({
      id: a.id,
      filename: a.filename,
      mime_type: a.mime_type,
    })),
    thread: {
      in_reply_to: payload.in_reply_to?.message_id ?? null,
    },
  };
}

interface KimiOrder {
  product_name?: string;
  quantity?: string;
  date_wanted?: string;
  status?: string;
}

interface StationWOrderLine {
  line_id?: number;
  product?: {
    raw_description?: string;
    status?: string;
    normalized?: {
      sku?: string | null;
    };
  };
  quantity?: number | string;
  date_wanted?: string;
  pricing?: {
    wanted_unit_price?: number | null;
    stock_unit_price?: number | null;
  };
}

interface KimiResponse {
  orders?: KimiOrder[];
  order_lines?: StationWOrderLine[];
  order_status?: string;
  missing_fields?: string[];
  prompt_text?: string;
}

interface QuoteCsvRow {
  order_id: string;
  sku_code: string;
  product_name: string;
  quantity: string;
}

interface QuoteFileSelection {
  path: string;
  filename: string;
  candidates: string[];
}

const STATIONW_QUOTE_ENDPOINT = "http://127.0.0.1:8000/quote";
const STATIONW_ORDERS_CSV = path.resolve(process.cwd(), "StationW/database/orders.csv");
const STATIONW_QUOTE_OUTPUT_DIR = path.resolve(process.cwd(), "StationW/quote/output");

function normalizeLookupValue(value: string): string {
  return value
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "");
}

function parseCsvLine(line: string): string[] {
  return line.split(";").map((value) => value.trim());
}

function loadQuoteOrderRows(): QuoteCsvRow[] {
  if (!fs.existsSync(STATIONW_ORDERS_CSV)) {
    return [];
  }

  const raw = fs.readFileSync(STATIONW_ORDERS_CSV, "utf8");
  const lines = raw.split(/\r?\n/).filter((line) => line.trim().length > 0);
  if (lines.length < 2) {
    return [];
  }

  const headers = parseCsvLine(lines[0]);
  const orderIdIndex = headers.indexOf("order_id");
  const skuIndex = headers.indexOf("sku_code");
  const productNameIndex = headers.indexOf("product_name");
  const quantityIndex = headers.indexOf("quantity");

  if (orderIdIndex < 0 || skuIndex < 0 || productNameIndex < 0 || quantityIndex < 0) {
    return [];
  }

  return lines.slice(1).map((line) => {
    const columns = parseCsvLine(line);
    return {
      order_id: columns[orderIdIndex] ?? "",
      sku_code: columns[skuIndex] ?? "",
      product_name: columns[productNameIndex] ?? "",
      quantity: columns[quantityIndex] ?? "",
    };
  }).filter((row) => row.order_id.length > 0);
}

function isQuoteResponseExploitable(response: KimiResponse | null): boolean {
  if (!response) {
    return false;
  }

  const status = response.order_status?.trim().toLowerCase() ?? "";
  if (status === "clarifying") {
    return false;
  }

  const orderLines = Array.isArray(response.order_lines) ? response.order_lines : [];
  if (orderLines.length === 0) {
    return false;
  }

  return orderLines.every((line) => (line.product?.status ?? "").toUpperCase() !== "NOT_FOUND");
}

function matchParsedLineToRow(line: StationWOrderLine, row: QuoteCsvRow): boolean {
  const parsedSku = normalizeLookupValue(line.product?.normalized?.sku ?? "");
  const parsedDescription = normalizeLookupValue(line.product?.raw_description ?? "");
  const rowSku = normalizeLookupValue(row.sku_code);
  const rowProductName = normalizeLookupValue(row.product_name);
  const parsedQuantity = String(line.quantity ?? "").trim();
  const rowQuantity = row.quantity.trim();

  const quantityMatches = parsedQuantity.length === 0 || parsedQuantity === rowQuantity;
  const skuMatches = parsedSku.length > 0 && rowSku.length > 0 && parsedSku === rowSku;
  const productMatches = parsedDescription.length > 0 && rowProductName.length > 0 && (
    parsedDescription === rowProductName ||
    parsedDescription.includes(rowProductName) ||
    rowProductName.includes(parsedDescription)
  );

  return quantityMatches && (skuMatches || productMatches);
}

function detectQuoteOrderId(response: KimiResponse | null): string | null {
  if (!isQuoteResponseExploitable(response)) {
    return null;
  }

  const parsedLines = (response?.order_lines ?? []).filter((line) => (line.product?.status ?? "").toUpperCase() !== "NOT_FOUND");
  const rows = loadQuoteOrderRows();
  if (rows.length === 0 || parsedLines.length === 0) {
    return null;
  }

  const groupedRows = new Map<string, QuoteCsvRow[]>();
  for (const row of rows) {
    const group = groupedRows.get(row.order_id) ?? [];
    group.push(row);
    groupedRows.set(row.order_id, group);
  }

  const candidates: Array<{ orderId: string; matchedLines: number }> = [];

  for (const [orderId, orderRows] of groupedRows.entries()) {
    const availableRows = [...orderRows];
    let matchedLines = 0;

    for (const parsedLine of parsedLines) {
      const matchIndex = availableRows.findIndex((row) => matchParsedLineToRow(parsedLine, row));
      if (matchIndex < 0) {
        continue;
      }

      availableRows.splice(matchIndex, 1);
      matchedLines += 1;
    }

    if (matchedLines === parsedLines.length) {
      candidates.push({ orderId, matchedLines });
    }
  }

  if (candidates.length !== 1) {
    return null;
  }

  return candidates[0].orderId;
}

async function callStationwQuoteEndpoint(orderId: string): Promise<boolean> {
  log("info", "quote_endpoint_call_started", { order_id: orderId, endpoint: `${STATIONW_QUOTE_ENDPOINT}/${encodeURIComponent(orderId)}` });

  try {
    const response = await fetch(`${STATIONW_QUOTE_ENDPOINT}/${encodeURIComponent(orderId)}`);

    if (!response.ok) {
      const body = await response.text();
      log("error", "quote_endpoint_call_error", {
        order_id: orderId,
        status: response.status,
        body: body.substring(0, 500),
      });
      return false;
    }

    log("info", "quote_endpoint_call_success", {
      order_id: orderId,
      status: response.status,
      content_type: response.headers.get("content-type"),
      x_order_id: response.headers.get("x-order-id"),
    });
    return true;
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    log("error", "quote_endpoint_call_error", { order_id: orderId, error: message });
    return false;
  }
}

function findQuotePdfForOrderId(outputDir: string, orderId: string): QuoteFileSelection | null {
  if (!fs.existsSync(outputDir)) {
    return null;
  }

  const entries = fs.readdirSync(outputDir, { withFileTypes: true });
  const candidates = entries
    .filter((entry) => entry.isFile())
    .map((entry) => {
      const filename = entry.name;
      const fullPath = path.join(outputDir, filename);
      return { filename, fullPath };
    })
    .filter(({ filename, fullPath }) => {
      if (filename === ".gitkeep") {
        return false;
      }
      if (filename.startsWith(".")) {
        return false;
      }
      if (!filename.toLowerCase().endsWith(".pdf")) {
        return false;
      }
      if (!filename.includes(orderId)) {
        return false;
      }
      const stats = fs.statSync(fullPath);
      return stats.size > 0;
    })
    .map(({ filename, fullPath }) => ({
      filename,
      fullPath,
      mtimeMs: fs.statSync(fullPath).mtimeMs,
    }))
    .sort((left, right) => right.mtimeMs - left.mtimeMs);

  if (candidates.length === 0) {
    return null;
  }

  return {
    path: candidates[0].fullPath,
    filename: candidates[0].filename,
    candidates: candidates.map((candidate) => candidate.filename),
  };
}

function buildReplyText(response: KimiResponse | null): string {
  // Note: parserFailed case removed - ack email already sent before this point
  // This function is only called if we have a valid parser response with actual content

  const orderLines = Array.isArray(response?.order_lines) ? response?.order_lines : [];
  if (orderLines.length > 0) {
    const lines = orderLines.map((orderLine) => {
      const product = orderLine.product?.raw_description?.trim() || "";
      const quantity = String(orderLine.quantity ?? "").trim();
      const dateWanted = orderLine.date_wanted?.trim() || "";
      const status = orderLine.product?.status?.trim() || "";
      return `- ${product} — quantity: ${quantity} — requested date: ${dateWanted} — status: ${status}`;
    });

    return `Good news — we have your requested items in stock.\n\nPlease find your quote attached.\n\nOrder summary:\n${lines.join("\n")}\n\nBest regards,`;
  }

  const orders = Array.isArray(response?.orders) ? response?.orders : [];
  if (orders.length === 0) {
    return "We have received your request, but we could not clearly identify the requested products.";
  }

  const lines = orders.map((order) => {
    const product = order.product_name?.trim() || "";
    const quantity = order.quantity?.trim() || "";
    const dateWanted = order.date_wanted?.trim() || "";
    const status = order.status?.trim() || "";
    return `- ${product} — quantity: ${quantity} — requested date: ${dateWanted} — status: ${status}`;
  });

  return `Good news — we have your requested items in stock.\n\nPlease find your quote attached.\n\nOrder summary:\n${lines.join("\n")}\n\nBest regards,`;
}

async function callStationwParser(orderText: string): Promise<KimiResponse | null> {
  log("info", "stationw_request_attempt", { endpoint: STATIONW_ENDPOINT, order_text_length: orderText.length, stationw_timeout_ms: STATIONW_TIMEOUT_MS });

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), STATIONW_TIMEOUT_MS);

  try {
    const res = await fetch(STATIONW_ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ order_text: orderText }),
      signal: controller.signal,
    });
    const raw = await res.text();
    log("info", "stationw_http_status", { status: res.status });
    log("info", "stationw_raw_body", { raw_body: raw });

    if (!res.ok) {
      log("error", "stationw_request_error", { status: res.status, response: raw });
      return null;
    }

    let parsed: KimiResponse;
    try {
      parsed = JSON.parse(raw) as KimiResponse;
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      log("error", "stationw_request_error", { error: message, response: raw });
      return null;
    }

    const detectedShape = Array.isArray(parsed.order_lines) && parsed.order_lines.length > 0
      ? "order_lines"
      : Array.isArray(parsed.orders) && parsed.orders.length > 0
        ? "orders"
        : "empty_or_invalid";
    const ordersCount = Array.isArray(parsed.orders) ? parsed.orders.length : 0;
    const orderLinesCount = Array.isArray(parsed.order_lines) ? parsed.order_lines.length : 0;
    log("info", "stationw_detected_shape", { detected_shape: detectedShape, orders_count: ordersCount, order_lines_count: orderLinesCount });
    return parsed;
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    log("error", "stationw_request_error", { error: message });
    return null;
  } finally {
    clearTimeout(timeout);
  }
}

export async function handleUnipileEmailWebhook(req: Request, res: Response) {
  const contentType = req.headers["content-type"] ?? "unknown";
  const authHeaderPresent = Boolean(req.headers["unipile-auth"]);
  log("info", "webhook_received", {
    method: req.method,
    path: req.path,
    content_type: contentType,
    auth_header: authHeaderPresent ? "present" : "absent",
  });

  const isJson = Boolean(req.is(["application/json", "application/*+json"]));
  if (!isJson) {
    log("warn", "unsupported_media_type", { content_type: contentType });
    res.status(415).json({ error: "Unsupported Media Type" });
    return;
  }

  // Secret check
  const skipCheck = process.env.SKIP_WEBHOOK_SECRET_CHECK === "true";
  const secret = process.env.WEBHOOK_SECRET;
  if (skipCheck) {
    log("warn", "webhook_secret_check_skipped");
  } else if (secret) {
    const incoming = req.headers["unipile-auth"] as string | undefined;
    if (incoming !== secret) {
      log("warn", "secret_mismatch", {
        auth_header: incoming ? "present" : "absent",
      });
      res.status(401).json({ error: "Unauthorized" });
      return;
    }
    log("info", "secret_ok");
  } else {
    log("warn", "webhook_secret_missing");
  }

  // Payload validation
  const validation = validatePayload(req.body);
  if (validation.errors.length > 0 || !validation.value) {
    log("error", "invalid_payload", { errors: validation.errors });
    res.status(400).json({ error: "Invalid payload", details: validation.errors });
    return;
  }

  const payload = validation.value;
  log("info", "payload_validated", {
    event: payload.event,
    account_id: payload.account_id,
    email_id: payload.email_id,
    message_id: payload.message_id,
    from: payload.from_attendee.identifier,
    to: payload.to_attendees[0]?.identifier ?? "",
  });

  if (payload.event !== "mail_received") {
    const skipMsg = payload.event === "mail_sent" ? "reply_skipped_mail_sent_event" : "event_ignored";
    log("info", skipMsg, { event: payload.event });
    res.json({ status: "ignored", event: payload.event });
    return;
  }

  const dedupeKey = `${payload.account_id}:${payload.message_id || payload.email_id}`;
  const dedupeEnabled = process.env.DISABLE_WEBHOOK_DEDUPE !== "true";
  const isDuplicate = isDuplicateMessage(dedupeKey);

  if (!dedupeEnabled) {
    log("warn", "duplicate_message_bypass_enabled", { dedupe_enabled: false, dedupe_key: dedupeKey });
    if (isDuplicate) {
      log("warn", "duplicate_message_would_have_been_skipped", { dedupe_enabled: false, dedupe_key: dedupeKey });
    }
  } else if (isDuplicate) {
    log("warn", "duplicate_message_skipped", { dedupe_enabled: true, dedupe_key: dedupeKey });
    res.json({ status: "duplicate" });
    return;
  }

  // Normalize
  let normalizedEmail: ReturnType<typeof normalize>;
  try {
    normalizedEmail = normalize(payload);
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    log("error", "normalize_failed", { error: message });
    res.status(500).json({ error: "Payload mapping failed" });
    return;
  }
  log("info", "payload_normalized", {
    mailbox: normalizedEmail.mailbox,
    from: normalizedEmail.from.email,
    has_attachments: normalizedEmail.has_attachments,
    thread_in_reply_to: normalizedEmail.thread.in_reply_to,
    available_ids: {
      email_id: normalizedEmail.email_id,
      provider_id: normalizedEmail.provider_id,
      message_id: normalizedEmail.message_id,
      thread_id: normalizedEmail.thread_id,
    },
  });

  // Anti-loop
  if (normalizedEmail.from.email === normalizedEmail.mailbox) {
    log("info", "reply_skipped_self_email", { from: normalizedEmail.from.email, mailbox: normalizedEmail.mailbox });
    res.json({ status: "skipped" });
    return;
  }

  // ================== IMMEDIATE ACK EMAIL SEND ==================
  // Send acknowledgement email immediately in the thread
  const ackEmailBody = "Hello,\n\nWe have received your request and our team is reviewing it.\nWe will get back to you shortly.\n\nBest regards,";
  const selectedAckReplyToId = normalizedEmail.provider_id || normalizedEmail.email_id || "";
  const ackReplyToSource = normalizedEmail.provider_id ? "provider_id" : normalizedEmail.email_id ? "email_id" : "none";

  // Build ACK reply subject
  const ackSubjectOriginal = normalizedEmail.subject ?? "";
  const ackSubjectCleaned = ackSubjectOriginal.replace(/^(?:\s*(?:Re:|Fwd:|FW:))+/i, "").trim();
  let ackSubjectFinal = "Re: Your request"; // Default fallback
  let ackSubjectSource = "fallback";

  if (ackSubjectCleaned.length > 0) {
    ackSubjectFinal = `Re: ${ackSubjectCleaned}`;
    ackSubjectSource = "original_email_subject";
    log("info", "ack_reply_subject_selected", {
      original: ackSubjectOriginal,
      cleaned: ackSubjectCleaned,
      final: ackSubjectFinal,
    });
  } else if (ackSubjectOriginal.length > 0) {
    log("info", "ack_reply_subject_fallback_used", {
      reason: "subject_only_had_prefixes",
      original: ackSubjectOriginal,
      fallback: ackSubjectFinal,
    });
  } else {
    log("info", "ack_reply_subject_fallback_used", {
      reason: "no_subject_provided",
      fallback: ackSubjectFinal,
    });
  }

  log("info", "ack_reply_send_attempt", {
    account_id: normalizedEmail.account_id,
    to: normalizedEmail.from.email,
    selected_reply_to_id: selectedAckReplyToId,
    selected_reply_to_source: ackReplyToSource,
    ack_subject_final: ackSubjectFinal,
    ack_subject_source: ackSubjectSource,
    body_length: ackEmailBody.length,
  });

  try {
    const { textBody: ackText, htmlBody: ackHtml } = formatEmailBody(ackEmailBody);
    await sendReply(
      normalizedEmail.account_id,
      normalizedEmail.from.email,
      ackSubjectFinal,
      ackHtml,
      selectedAckReplyToId,
      undefined,
      ackText
    );
    log("info", "ack_reply_send_success", {
      to: normalizedEmail.from.email,
      reply_to_source: ackReplyToSource,
    });
  } catch (err) {
    const e = err as Record<string, unknown> & { response?: Record<string, unknown> };
    const response = (e.response ?? {}) as Record<string, unknown>;
    log("error", "ack_reply_send_error", {
      to: normalizedEmail.from.email,
      name: e.name,
      message: e.message,
      code: e.code,
      status: e.status,
      response_status: response.status,
    });
    // Don't block the flow - continue with normal processing even if ack fails
  }
  // =================== END ACK EMAIL SEND =======================

  // =================== THREAD CONTEXT HANDLING ====================
  const isFollowup = !!normalizedEmail.thread.in_reply_to;
  let orderText =
    normalizedEmail.body_text?.trim() ||
    normalizedEmail.body_html?.trim() ||
    "";

  let contextPreserved = false;
  let previousMessagesText = "";
  let parserInputPreview = "";
  let threadMessagesCount = 0;

  if (isFollowup && normalizedEmail.thread_id) {
    log("info", "followup_original_message_fetch_started", {
      thread_id: normalizedEmail.thread_id,
      current_email_id: normalizedEmail.email_id,
      current_body_length: orderText.length,
      current_body_preview: orderText.substring(0, 100),
    });

    try {
      const threadMessages = await getThreadMessages(normalizedEmail.account_id, normalizedEmail.thread_id);
      threadMessagesCount = threadMessages.length;

      log("info", "followup_thread_messages_fetched", {
        thread_id: normalizedEmail.thread_id,
        total_messages_count: threadMessages.length,
        current_email_id: normalizedEmail.email_id,
        messages_preview: threadMessages.map((m, idx) => ({
          index: idx,
          email_id: m.email_id,
          from: m.from,
          is_current: m.email_id === normalizedEmail.email_id,
          body_length: (m.body_text || m.body_html || "").length,
        })),
      });

      if (threadMessages.length > 0) {
        // Reconstruct thread context by combining previous messages
        // Skip the current message (most recent) and include earlier ones
        const previousMessages = threadMessages.filter(
          (msg) => msg.email_id !== normalizedEmail.email_id
        );

        log("info", "followup_previous_messages_filtered", {
          thread_id: normalizedEmail.thread_id,
          total_messages: threadMessages.length,
          previous_messages_count: previousMessages.length,
          current_email_id: normalizedEmail.email_id,
          filtered_message_ids: previousMessages.map((m) => m.email_id),
        });

        if (previousMessages.length > 0) {
          // Build combined context with clear separation
          previousMessagesText = previousMessages
            .map((msg, idx) => {
              const sender = msg.from ? `[${msg.from}]` : "[Previous message]";
              const subject = msg.subject ? `Subject: ${msg.subject}` : "";
              const body = msg.body_text || msg.body_html || "(no body)";
              const msgBlock = `--- Previous message ${idx + 1} ${sender} ${subject} ---\n${body}`;
              
              log("info", "followup_previous_message_block", {
                index: idx,
                email_id: msg.email_id,
                sender,
                body_length: body.length,
                subject,
              });

              return msgBlock;
            })
            .join("\n\n");

          // Combine: previous context + current follow-up
          orderText = `${previousMessagesText}\n\n--- Current follow-up message ---\n${orderText}`;
          contextPreserved = true;

          log("info", "followup_original_message_fetch_success", {
            thread_id: normalizedEmail.thread_id,
            previous_messages_count: previousMessages.length,
            original_current_body_length: (normalizedEmail.body_text?.trim() || normalizedEmail.body_html?.trim() || "").length,
            combined_text_length: orderText.length,
            contextPreserved: true,
          });

          log("info", "followup_original_order_lines_preserved", {
            thread_id: normalizedEmail.thread_id,
            previous_messages_included: previousMessages.length,
            combined_context: true,
            total_input_size_bytes: Buffer.byteLength(orderText, 'utf8'),
          });

          // Generate preview of what's being sent to parser
          const preview = orderText.substring(0, 500);
          parserInputPreview = preview.length < orderText.length ? preview + "\n...[truncated]" : preview;

          log("info", "followup_parser_input_preview", {
            thread_id: normalizedEmail.thread_id,
            total_input_length: orderText.length,
            preview_length: parserInputPreview.length,
            includes_previous_messages: true,
            preview: parserInputPreview,
          });

          log("info", "followup_full_parser_input", {
            thread_id: normalizedEmail.thread_id,
            full_text: orderText,
            total_length: orderText.length,
          });
        }
      } else {
        log("info", "followup_original_message_fetch_success", {
          thread_id: normalizedEmail.thread_id,
          previous_messages_count: 0,
          combined_text_length: orderText.length,
          contextPreserved: false,
          note: "Thread fetch returned no messages, using current message only",
        });
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      log("warn", "followup_original_message_fetch_error", {
        thread_id: normalizedEmail.thread_id,
        error: message,
        note: "Will use current message only",
      });
    }
  } else {
    log("info", "followup_context_lookup_started", {
      thread_id: normalizedEmail.thread_id,
      in_reply_to: null,
      is_followup: false,
    });
  }

  log("info", "stationw_request_about_to_send", {
    is_followup: isFollowup,
    context_preserved: contextPreserved,
    thread_messages_count: threadMessagesCount,
    input_text_length: orderText.length,
    input_preview: orderText.substring(0, 200),
  });

  const parserResponse = await callStationwParser(orderText);
  
  // If parser failed, don't send a business reply (ack already sent)
  if (!parserResponse) {
    log("info", "business_reply_skipped_parser_failed", { ack_already_sent: true });
    res.json({ status: "ok", reply_sent: true });
    return;
  }

  let replyBody: string;
  let replyBodySource: string;
  
  if (parserResponse?.prompt_text?.trim()) {
    replyBody = parserResponse.prompt_text;
    replyBodySource = "kimi_message";
  } else {
    replyBody = buildReplyText(parserResponse);
    replyBodySource = "derived_status_message";
  }
  
  log("info", "stationw_reply_built", {
    reply_body_source: replyBodySource,
    body_length: replyBody.length,
    missing_fields: parserResponse?.missing_fields ?? [],
    orders_count: parserResponse?.orders?.length ?? 0,
    order_lines_count: parserResponse?.order_lines?.length ?? 0,
    parser_success: parserResponse !== null,
  });

  const dryRun = process.env.DRY_RUN_SEND_REPLY === "true";
  const reply_subject_original = normalizedEmail.subject ?? "";
  const reply_subject_normalized = reply_subject_original.replace(/^(?:\s*(?:Re:|Fwd:|FW:))+/i, "").trim();

  let quoteFile: QuoteFileSelection | null = null;
  let pdfAttachmentDecision: string;
  let pdfAttachmentReason: string;

  const shouldAttachPdf =
    parserResponse !== null &&
    parserResponse.order_status !== "clarifying" &&
    !parserResponse.prompt_text?.trim() &&
    (!parserResponse.missing_fields || parserResponse.missing_fields.length === 0);

  if (shouldAttachPdf) {
    pdfAttachmentDecision = "true";
    const quoteOrderId = detectQuoteOrderId(parserResponse);

    if (quoteOrderId) {
      pdfAttachmentReason = "ok";
      log("info", "quote_order_id_detected", { order_id: quoteOrderId });

      const quoteEndpointOk = await callStationwQuoteEndpoint(quoteOrderId);
      if (quoteEndpointOk) {
        log("info", "quote_output_search_started", { search_path: STATIONW_QUOTE_OUTPUT_DIR, order_id: quoteOrderId });
        quoteFile = findQuotePdfForOrderId(STATIONW_QUOTE_OUTPUT_DIR, quoteOrderId);
        log("info", "quote_output_candidates", {
          order_id: quoteOrderId,
          candidates: quoteFile?.candidates ?? [],
        });

        if (quoteFile) {
          log("info", "quote_output_selected", { order_id: quoteOrderId, filename: quoteFile.filename, path: quoteFile.path });
        } else {
          pdfAttachmentReason = "pdf_missing";
          log("info", "quote_attachment_missing", { order_id: quoteOrderId, search_path: STATIONW_QUOTE_OUTPUT_DIR, reason: "no_matching_pdf_found" });
        }
      } else {
        pdfAttachmentReason = "quote_failed";
        log("info", "quote_attachment_missing", { order_id: quoteOrderId, search_path: STATIONW_QUOTE_OUTPUT_DIR, reason: "quote_endpoint_call_failed" });
      }
    } else {
      pdfAttachmentReason = "no_order_id";
      log("info", "quote_attachment_missing", { search_path: STATIONW_QUOTE_OUTPUT_DIR, reason: "no_exploitable_order_id" });
    }
  } else {
    pdfAttachmentDecision = "false";
    if (parserResponse?.order_status === "clarifying") {
      pdfAttachmentReason = "clarifying";
    } else if (parserResponse?.prompt_text?.trim()) {
      pdfAttachmentReason = "kimi_clarification_message";
    } else if (parserResponse?.missing_fields && parserResponse.missing_fields.length > 0) {
      pdfAttachmentReason = "missing_fields";
    } else {
      pdfAttachmentReason = "parser_failed";
    }
    log("info", "quote_attachment_blocked", { reason: pdfAttachmentReason, order_status: parserResponse?.order_status ?? null });
  }

  log("info", "pdf_attachment_decision", { decision: pdfAttachmentDecision, reason: pdfAttachmentReason });

  // Log follow-up specific decision
  if (isFollowup && pdfAttachmentReason === "clarifying") {
    log("info", "followup_quote_generation_blocked", {
      reason: "clarifying_status_detected",
      is_followup: true,
      context_preserved: contextPreserved,
      order_lines_found: parserResponse?.order_lines?.length ?? 0,
      missing_fields: parserResponse?.missing_fields ?? [],
      note: "Follow-up without products - requesting clarification from customer",
    });
  } else if (pdfAttachmentDecision === "true") {
    log("info", "quote_generation_ready", {
      is_followup: isFollowup,
      context_preserved: contextPreserved,
      order_lines_count: parserResponse?.order_lines?.length ?? 0,
      quote_order_id: quoteFile ? "found" : "pending",
    });
  } else if (pdfAttachmentReason === "missing_fields") {
    log("info", "quote_generation_skipped_missing_order_lines", {
      is_followup: isFollowup,
      context_preserved: contextPreserved,
      missing_fields: parserResponse?.missing_fields ?? [],
      note: "Waiting for missing information before generating quote",
    });
  }

  log("info", "simple_email_send_attempt", {
    account_id: normalizedEmail.account_id,
    to: normalizedEmail.from.email,
    reply_subject_original: reply_subject_original,
    dry_run: dryRun,
    has_attachment: !!quoteFile,
  });

  if (dryRun) {
    log("info", "reply_dry_run", { to: normalizedEmail.from.email });
    res.json({ status: "ok", reply_sent: false, dry_run: true });
    return;
  }

  try {
    const selectedReplyToId = normalizedEmail.provider_id || normalizedEmail.email_id || "";
    const replyToSource = normalizedEmail.provider_id ? "provider_id" : normalizedEmail.email_id ? "email_id" : "none";

    // Unipile requires a non-empty subject even on replies; "errors/invalid_reply_subject"
    // is returned when the multipart has no subject field.
    const reply_subject_has_re_prefix = /^(?:\s*(?:Re:|Fwd:|FW:))/i.test(reply_subject_original);
    const reply_subject_base = reply_subject_normalized || reply_subject_original.trim();
    const reply_subject_sent = reply_subject_base ? `Re: ${reply_subject_base}` : "Re:";
    const reply_subject_mode = selectedReplyToId ? "reply_with_re_prefix" : "new_message";

    log("info", "email_reply_thread_selection", { 
      selected_reply_to_value: selectedReplyToId,
      selected_reply_to_source: replyToSource,
      selected_reply_to_is_mime_message_id: false,
      available_provider_id: normalizedEmail.provider_id || null,
      available_email_id: normalizedEmail.email_id || null,
      available_mime_message_id: normalizedEmail.message_id || null,
      thread_id: normalizedEmail.thread_id || null,
    });

    log("info", "reply_subject_info", {
      reply_subject_original: reply_subject_original,
      reply_subject_sent: reply_subject_sent,
      reply_subject_mode: reply_subject_mode,
      reply_subject_has_re_prefix: reply_subject_has_re_prefix,
    });

    log("info", "quote_attachment_send_attempt", { 
      pdf_attachment: pdfAttachmentDecision, 
      pdf_reason: pdfAttachmentReason,
      has_attachment: !!quoteFile, 
      filename: quoteFile?.filename ?? null 
    });

    const { textBody: replyText, htmlBody: replyHtml } = formatEmailBody(replyBody);
    await sendReply(
      normalizedEmail.account_id,
      normalizedEmail.from.email,
      reply_subject_sent ?? "",
      replyHtml,
      selectedReplyToId,
      quoteFile?.path,
      replyText
    );
    if (quoteFile) {
      log("info", "quote_attachment_send_success", { filename: quoteFile.filename });
    }
    log("info", "simple_email_send_success", {
      to: normalizedEmail.from.email,
      is_followup: isFollowup,
      reply_type: replyBodySource,
      has_pdf: !!quoteFile,
    });
    res.json({ status: "ok", reply_sent: true });
  } catch (err) {
    const e = err as Record<string, unknown> & { response?: Record<string, unknown> };
    const response = (e.response ?? {}) as Record<string, unknown>;
    log("error", "simple_email_send_error", {
      name: e.name,
      message: e.message,
      code: e.code,
      status: e.status,
      response_status: response.status,
      response_data: response.data,
    });
    const detailMessage = typeof e.message === "string" ? e.message : String(err);
    res.status(500).json({ error: "Email send failed", detail: detailMessage });
  }
}
