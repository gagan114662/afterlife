// backfill.ts — historical ingest for one selected contact
//
// Processes a batch of WAMessage records (from Baileys history sync or fixtures),
// normalizes media, and upserts into MongoDB.  The operation is fully idempotent:
// running it twice on the same message set produces identical DB state.
import { WAMessage } from '@whiskeysockets/baileys';
import { upsertMessage, SyncedMessage } from './db';
import { classifyMessage } from './personal';
import { normalizeMedia } from './normalizer';

export interface BackfillOptions {
  /** WhatsApp JID of the contact, e.g. "15550001234@s.whatsapp.net" */
  jid: string;
  /** Human-readable display name used for file paths and DB records */
  contactName: string;
  /** Historical messages to process (order does not matter) */
  messages: WAMessage[];
  /** Root directory for downloaded/converted media files */
  mediaDir?: string;
}

export interface BackfillResult {
  processed: number;
  skipped: number;
}

/**
 * Backfill all historical messages for one contact.
 *
 * - Classifies each message by type (text / voice_note / photo / video)
 * - Downloads and normalises media to a stable, idempotent path
 * - Upserts each record into MongoDB (keyed on messageId + jid)
 *
 * Returns counts of processed and skipped (unclassified) messages.
 */
export async function backfillContact(opts: BackfillOptions): Promise<BackfillResult> {
  const { jid, contactName, messages, mediaDir = './media' } = opts;
  let processed = 0;
  let skipped = 0;

  for (const msg of messages) {
    const classified = classifyMessage(msg);
    if (!classified) {
      skipped++;
      continue;
    }

    const messageId = msg.key.id || '';
    const timestamp =
      typeof msg.messageTimestamp === 'number'
        ? msg.messageTimestamp
        : Number(msg.messageTimestamp ?? 0);

    let mediaPath: string | null = null;
    if (
      classified.type === 'voice_note' ||
      classified.type === 'photo' ||
      classified.type === 'video'
    ) {
      mediaPath = await normalizeMedia(msg, messageId, contactName, classified.type, mediaDir);
    }

    const synced: SyncedMessage = {
      jid,
      contact: contactName,
      messageId,
      timestamp,
      from: msg.key.fromMe ? 'me' : 'them',
      type: classified.type,
      content: classified.content,
      media_path: mediaPath,
      syncedAt: new Date(),
    };

    await upsertMessage(synced);
    processed++;
  }

  return { processed, skipped };
}
