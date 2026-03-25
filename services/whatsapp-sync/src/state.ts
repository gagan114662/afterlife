import { Collection } from 'mongodb';
import { getDb } from './db';

export enum UserState {
  INIT = 'INIT',         // New user, never seen before
  QR_SENT = 'QR_SENT',  // QR code sent, waiting for scan
  LINKED = 'LINKED',    // Personal WhatsApp linked, triggering sync
  SYNCING = 'SYNCING',  // Background sync in progress
  ACTIVE = 'ACTIVE',    // Sync complete, ready for conversations
}

export interface UserStateDoc {
  jid: string;
  state: UserState;
  contact_count: number;
  selected_contact: string | null;
  session_id: string | null;
  updated_at: string;
}

async function getStateCollection(): Promise<Collection<UserStateDoc>> {
  const db = await getDb();
  return db.collection<UserStateDoc>('user_state');
}

export async function getUserState(jid: string): Promise<UserStateDoc> {
  const col = await getStateCollection();
  const doc = await col.findOne({ jid });
  if (!doc) {
    return {
      jid,
      state: UserState.INIT,
      contact_count: 0,
      selected_contact: null,
      session_id: null,
      updated_at: new Date().toISOString(),
    };
  }
  return doc;
}

export async function setUserState(
  jid: string,
  updates: Partial<Omit<UserStateDoc, 'jid'>>
): Promise<void> {
  const col = await getStateCollection();
  await col.updateOne(
    { jid },
    {
      $set: {
        ...updates,
        updated_at: new Date().toISOString(),
      },
    },
    { upsert: true }
  );
}

export async function ensureStateIndex(): Promise<void> {
  const col = await getStateCollection();
  await col.createIndex({ jid: 1 }, { unique: true });
}
