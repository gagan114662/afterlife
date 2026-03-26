// db.ts unit tests — mock the MongoDB client so no real DB is needed
import { SyncedMessage } from '../db';

// We mock the mongodb module before importing db
const mockUpdateOne = jest.fn().mockResolvedValue({ upsertedCount: 1 });
const mockCollection = jest.fn().mockReturnValue({ updateOne: mockUpdateOne });
const mockDb = jest.fn().mockReturnValue({ collection: mockCollection });
const mockConnect = jest.fn().mockResolvedValue(undefined);
const mockClose = jest.fn().mockResolvedValue(undefined);

jest.mock('mongodb', () => ({
  MongoClient: jest.fn().mockImplementation(() => ({
    connect: mockConnect,
    db: mockDb,
    close: mockClose,
  })),
}));

// Import after mock setup
// eslint-disable-next-line @typescript-eslint/no-require-imports
const { upsertMessage, closeDb } = require('../db');

describe('upsertMessage', () => {
  afterEach(async () => {
    await closeDb();
    jest.clearAllMocks();
    // Reset module state so next test gets a fresh connection
    jest.resetModules();
  });

  it('calls updateOne with upsert:true', async () => {
    const msg: SyncedMessage = {
      jid: '15550001234@s.whatsapp.net',
      contact: '15550001234',
      messageId: 'abc123',
      timestamp: 1700000000,
      from: 'them',
      type: 'text',
      content: 'Hello',
      media_path: null,
      syncedAt: new Date('2024-01-01'),
    };

    await upsertMessage(msg);

    expect(mockUpdateOne).toHaveBeenCalledWith(
      { messageId: 'abc123', jid: '15550001234@s.whatsapp.net' },
      { $set: msg },
      { upsert: true }
    );
  });

  it('uses whatsapp_messages collection', async () => {
    const msg: SyncedMessage = {
      jid: '15550001234@s.whatsapp.net',
      contact: '15550001234',
      messageId: 'xyz789',
      timestamp: 1700000001,
      from: 'me',
      type: 'text',
      content: 'Hi',
      media_path: null,
      syncedAt: new Date(),
    };

    await upsertMessage(msg);

    expect(mockCollection).toHaveBeenCalledWith('whatsapp_messages');
  });
});
