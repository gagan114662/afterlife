import { MongoClient, Db, Collection } from 'mongodb';

const MONGODB_URI = process.env.MONGODB_URI || 'mongodb://localhost:27017';
const MONGODB_DB = process.env.MONGODB_DB || 'afterlife';

let client: MongoClient | null = null;
let db: Db | null = null;

export async function getDb(): Promise<Db> {
  if (!db) {
    client = new MongoClient(MONGODB_URI);
    await client.connect();
    db = client.db(MONGODB_DB);
  }
  return db;
}

export interface SyncedMessage {
  jid: string;
  contact: string;
  messageId: string;
  timestamp: number;
  from: 'me' | 'them';
  type: 'text' | 'voice_note' | 'photo' | 'video' | 'other';
  content: string;
  media_path: string | null;
  syncedAt: Date;
}

export async function upsertMessage(msg: SyncedMessage): Promise<void> {
  const database = await getDb();
  const col: Collection<SyncedMessage> = database.collection('whatsapp_messages');
  await col.updateOne(
    { messageId: msg.messageId, jid: msg.jid },
    { $set: msg },
    { upsert: true }
  );
}

export async function closeDb(): Promise<void> {
  if (client) {
    await client.close();
    client = null;
    db = null;
  }
}
