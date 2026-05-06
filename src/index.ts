import "dotenv/config";
import express, { Request, Response } from "express";
import { sendTestEmail } from "./unipileClient";
import { handleUnipileEmailWebhook } from "./emailWebhook";

const app = express();
const PORT = process.env.PORT ?? 3000;
const bodyLimit = process.env.JSON_BODY_LIMIT ?? "1mb";

app.use(express.json({ limit: bodyLimit }));

app.use((err: unknown, _req: Request, res: Response, next: (err?: unknown) => void) => {
  if (err && typeof err === "object" && "type" in err) {
    const errType = (err as { type?: string }).type;
    if (errType === "entity.too.large") {
      res.status(413).json({ error: "Payload too large" });
      return;
    }
  }

  if (err instanceof SyntaxError && "body" in err) {
    res.status(400).json({ error: "Invalid JSON body" });
    return;
  }

  next(err);
});

app.get("/health", (_req, res) => {
  res.json({ status: "ok" });
});

app.post("/send-test-email", async (req: Request, res: Response) => {
  const { accountId, to } = req.body as { accountId?: string; to?: string };

  if (!accountId || !to) {
    res.status(400).json({ error: "accountId and to are required" });
    return;
  }

  try {
    await sendTestEmail(accountId, to);
    res.json({ status: "sent" });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    res.status(500).json({ error: message });
  }
});

app.post("/webhooks/unipile/email", handleUnipileEmailWebhook);

app.listen(PORT, () => {
  const webhookSecret = process.env.WEBHOOK_SECRET ? "yes" : "no";
  const unipileDsn = process.env.UNIPILE_DSN ?? "";
  const unipileToken = process.env.UNIPILE_ACCESS_TOKEN ?? "";
  const dryRun = process.env.DRY_RUN_SEND_REPLY === "true";

  console.log(`Server running on port ${PORT}`);
  console.log(`StationW parser: http://127.0.0.1:8000/parse-order`);
  console.log(`WEBHOOK_SECRET configured: ${webhookSecret}`);
  console.log(`DRY_RUN_SEND_REPLY: ${dryRun}`);

  if (!unipileDsn || !unipileToken) {
    console.warn(
      "WARNING: UNIPILE_DSN or UNIPILE_ACCESS_TOKEN is not set — " +
      "sendReply and send-test-email will throw if called"
    );
  }
});
