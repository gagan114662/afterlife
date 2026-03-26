// normalizer.ts — stable media paths and format conversion for WhatsApp media
import * as fs from 'fs';
import * as path from 'path';
import { WAMessage, downloadMediaMessage } from '@whiskeysockets/baileys';
import { convertOggToWav } from './audio';

/**
 * Returns the stable, deterministic filesystem path for a media file.
 * Path is keyed on (contactName, messageId) so replay is idempotent.
 */
export function stableMediaPath(
  mediaDir: string,
  contactName: string,
  messageId: string,
  ext: string
): string {
  return path.join(mediaDir, contactName, `${messageId}.${ext}`);
}

/**
 * Download and normalize media for a single message.
 * - voice_note: download OGG → convert to WAV (16kHz mono PCM)
 * - photo: download and save as JPG
 * - video: download and save as MP4
 *
 * Idempotent: if the output file already exists, skip download/conversion.
 * Returns the stable output path, or null if download fails.
 */
export async function normalizeMedia(
  msg: WAMessage,
  messageId: string,
  contactName: string,
  type: 'voice_note' | 'photo' | 'video',
  mediaDir: string
): Promise<string | null> {
  const contactMediaDir = path.join(mediaDir, contactName);
  if (!fs.existsSync(contactMediaDir)) {
    fs.mkdirSync(contactMediaDir, { recursive: true });
  }

  if (type === 'voice_note') {
    const wavPath = stableMediaPath(mediaDir, contactName, messageId, 'wav');
    if (fs.existsSync(wavPath)) return wavPath;

    const oggPath = stableMediaPath(mediaDir, contactName, messageId, 'ogg');
    try {
      const buffer = await downloadMediaMessage(msg, 'buffer', {});
      fs.writeFileSync(oggPath, buffer as Buffer);
      await convertOggToWav(oggPath, wavPath);
      fs.unlinkSync(oggPath);
      return wavPath;
    } catch (err) {
      if (fs.existsSync(oggPath)) fs.unlinkSync(oggPath);
      return null;
    }
  }

  if (type === 'photo') {
    const jpgPath = stableMediaPath(mediaDir, contactName, messageId, 'jpg');
    if (fs.existsSync(jpgPath)) return jpgPath;

    try {
      const buffer = await downloadMediaMessage(msg, 'buffer', {});
      fs.writeFileSync(jpgPath, buffer as Buffer);
      return jpgPath;
    } catch {
      return null;
    }
  }

  if (type === 'video') {
    const mp4Path = stableMediaPath(mediaDir, contactName, messageId, 'mp4');
    if (fs.existsSync(mp4Path)) return mp4Path;

    try {
      const buffer = await downloadMediaMessage(msg, 'buffer', {});
      fs.writeFileSync(mp4Path, buffer as Buffer);
      return mp4Path;
    } catch {
      return null;
    }
  }

  return null;
}
