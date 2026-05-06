import { UnipileClient } from "unipile-node-sdk";
import fs from "fs";
import path from "path";

let client: UnipileClient | null = null;

function getClient(): UnipileClient {
  const dsn = process.env.UNIPILE_DSN;
  const token = process.env.UNIPILE_ACCESS_TOKEN;

  if (!dsn || !token) {
    throw new Error("Missing required env vars: UNIPILE_DSN and UNIPILE_ACCESS_TOKEN");
  }

  if (!client) {
    client = new UnipileClient(dsn, token);
  }

  return client;
}

export async function sendTestEmail(accountId: string, to: string): Promise<void> {
  const client = getClient();
  await client.email.send({
    account_id: accountId,
    to: [{ identifier: to }],
    subject: "MroPilot test",
    body: "Hello from MroPilot gateway",
  });
}

export function findLatestQuoteFile(exportsDir: string): { path: string; filename: string } | null {
  try {
    if (!fs.existsSync(exportsDir)) {
      return null;
    }
    const files = fs.readdirSync(exportsDir, { withFileTypes: true });
    if (files.length === 0) {
      return null;
    }
    // Filter: only regular files, exclude .gitkeep and hidden files, prefer PDF
    const regularFiles = files.filter((f) => {
      if (!f.isFile()) return false;
      if (f.name === ".gitkeep") return false;
      if (f.name.startsWith(".")) return false;
      // Only PDF files for quote
      if (!f.name.toLowerCase().endsWith(".pdf")) return false;
      const fullPath = path.join(exportsDir, f.name);
      const stat = fs.statSync(fullPath);
      if (stat.size === 0) return false; // Ignore empty files
      return true;
    });

    if (regularFiles.length === 0) {
      return null;
    }

    // Sort by modification time, newest first
    const sorted = regularFiles.sort((a, b) => {
      const aStats = fs.statSync(path.join(exportsDir, a.name));
      const bStats = fs.statSync(path.join(exportsDir, b.name));
      return bStats.mtimeMs - aStats.mtimeMs;
    });

    const latest = sorted[0];
    const fullPath = path.join(exportsDir, latest.name);
    return { path: fullPath, filename: latest.name };
  } catch (err) {
    return null;
  }
}

/**
 * Escape HTML special characters to prevent injection
 * @param text Raw text to escape
 * @returns HTML-safe escaped string
 */
function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/**
 * Format email body for proper rendering in email clients
 * - Plain text: preserves \n exactly as-is (normalized: \r\n → \n)
 * - HTML: safely escapes content, converts \n\n → </p><p> and \n → <br>
 * @param rawBody Raw email text (may contain \n, \r\n, or be single-line)
 * @returns Object with textBody (plain) and htmlBody (HTML-safe formatted)
 */
export function formatEmailBody(rawBody: string): { textBody: string; htmlBody: string } {
  // Normalize line breaks: \r\n and \r → \n
  const normalized = rawBody.replace(/\r\n/g, "\n").replace(/\r/g, "\n");

  // Plain text: use normalized as-is
  const textBody = normalized;

  // HTML: escape first, then convert newlines to markup
  const escaped = escapeHtml(normalized);
  let htmlBody = escaped;

  if (htmlBody.includes("\n")) {
    // Has newlines: \n\n → </p><p>, then \n → <br>
    htmlBody = `<p>${htmlBody.replace(/\n\n/g, "</p><p>").replace(/\n/g, "<br>")}</p>`;
  } else {
    // Single line: just wrap in <p>
    htmlBody = `<p>${htmlBody}</p>`;
  }

  return { textBody, htmlBody };
}

export async function sendReply(
  accountId: string,
  to: string,
  subject: string,
  body: string,
  replyToEmailId: string,
  attachmentPath?: string,
  bodyPlain?: string
): Promise<void> {
  const dsn = process.env.UNIPILE_DSN;
  const token = process.env.UNIPILE_ACCESS_TOKEN;
  if (!dsn || !token) {
    throw new Error("Missing UNIPILE_DSN or UNIPILE_ACCESS_TOKEN");
  }

  const url = `${dsn.replace(/\/$/, "")}/api/v1/emails`;
  const toJson = JSON.stringify([{ identifier: to }]);

  const isReply = Boolean(replyToEmailId);
  const subjectToSend = subject && subject.trim().length > 0 ? subject : "Re:";

  const formData = new FormData();
  formData.append("account_id", accountId);
  formData.append("subject", subjectToSend);
  formData.append("body", body);
  if (bodyPlain) {
    formData.append("body_plain", bodyPlain);
  }
  formData.append("to", toJson);
  if (isReply) {
    formData.append("reply_to", replyToEmailId);
  }

  if (attachmentPath && fs.existsSync(attachmentPath)) {
    const buffer = fs.readFileSync(attachmentPath);
    const filename = path.basename(attachmentPath);
    formData.append("attachments", new Blob([buffer], { type: "application/pdf" }), filename);
  }

  const formDataKeysSeen: string[] = [];
  let subjectFieldValue: string | null = null;
  let replyToFieldValue: string | null = null;
  for (const [k, v] of formData.entries()) {
    formDataKeysSeen.push(k);
    if (k === "subject" && typeof v === "string") subjectFieldValue = v;
    if (k === "reply_to" && typeof v === "string") replyToFieldValue = v;
  }

  console.log(JSON.stringify({
    level: "info",
    message: "unipile_http_payload_keys",
    method: "POST",
    url,
    content_type_sent: "multipart/form-data (auto by FormData)",
    threading_mode: isReply ? "reply_to_parent" : "new_message",
    parent_message_id: replyToEmailId || null,
    payload_keys: formDataKeysSeen,
    subject_field_present_in_http_payload: formDataKeysSeen.includes("subject"),
    subject_field_value_preview: subjectFieldValue ? subjectFieldValue.substring(0, 120) : null,
    reply_to_field_value: replyToFieldValue,
    body_length: body.length,
    attachment_filename: attachmentPath ? path.basename(attachmentPath) : null,
  }));

  const res = await fetch(url, {
    method: "POST",
    headers: { "X-API-KEY": token, "accept": "application/json" },
    body: formData,
  });

  const responseText = await res.text();
  const responseHeaders = Object.fromEntries(res.headers.entries());

  console.log(JSON.stringify({
    level: res.ok ? "info" : "error",
    message: "unipile_http_response",
    status: res.status,
    status_text: res.statusText,
    headers: responseHeaders,
    body: responseText.substring(0, 2000),
  }));

  if (!res.ok) {
    throw new Error(`Unipile HTTP ${res.status} ${res.statusText}: ${responseText.substring(0, 500)}`);
  }
}

export interface ThreadMessage {
  email_id: string;
  message_id?: string;
  body_text?: string;
  body_html?: string;
  from?: string;
  subject?: string;
  date?: string;
}

/**
 * Fetch messages from a thread via Unipile API
 * Returns array of messages in the thread
 */
export async function getThreadMessages(
  accountId: string,
  threadId: string
): Promise<ThreadMessage[]> {
  const dsn = process.env.UNIPILE_DSN;
  const token = process.env.UNIPILE_ACCESS_TOKEN;
  if (!dsn || !token) {
    throw new Error("Missing UNIPILE_DSN or UNIPILE_ACCESS_TOKEN");
  }

  try {
    const url = `${dsn.replace(/\/$/, "")}/api/v1/threads/${threadId}`;
    
    console.log(JSON.stringify({
      level: "info",
      message: "unipile_thread_fetch_request",
      url,
      account_id: accountId,
      thread_id: threadId,
    }));

    const res = await fetch(url, {
      method: "GET",
      headers: {
        "X-API-KEY": token,
        "accept": "application/json",
      },
    });

    const responseStatus = res.status;
    const responseText = await res.text();

    console.log(JSON.stringify({
      level: res.ok ? "info" : "warn",
      message: "unipile_thread_fetch_response",
      status: responseStatus,
      response_length: responseText.length,
      response_preview: responseText.substring(0, 500),
    }));

    if (!res.ok) {
      console.log(JSON.stringify({
        level: "warn",
        message: "unipile_thread_fetch_failed",
        status: responseStatus,
        url,
        response: responseText.substring(0, 1000),
        note: "Will proceed with current email only",
      }));
      return [];
    }

    let data: Record<string, unknown>;
    try {
      data = JSON.parse(responseText) as Record<string, unknown>;
    } catch (parseErr) {
      console.log(JSON.stringify({
        level: "error",
        message: "unipile_thread_response_parse_error",
        error: parseErr instanceof Error ? parseErr.message : String(parseErr),
        response_preview: responseText.substring(0, 500),
      }));
      return [];
    }

    const messages = (data.messages as ThreadMessage[]) || [];

    console.log(JSON.stringify({
      level: "info",
      message: "unipile_thread_fetch_success",
      thread_id: threadId,
      messages_count: messages.length,
      messages_structure: messages.map((m, idx) => ({
        index: idx,
        email_id: m.email_id,
        has_body_text: !!m.body_text,
        has_body_html: !!m.body_html,
        from: m.from,
        body_text_length: m.body_text?.length ?? 0,
      })),
    }));

    return messages;
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    console.log(JSON.stringify({
      level: "warn",
      message: "unipile_thread_fetch_error",
      error: message,
      note: "Will proceed with current email only",
    }));
    return [];
  }
}
