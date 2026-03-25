import { UserState } from '../state';

// Pure enum tests — no DB needed
describe('UserState enum', () => {
  it('has the 5 required states', () => {
    expect(UserState.INIT).toBe('INIT');
    expect(UserState.QR_SENT).toBe('QR_SENT');
    expect(UserState.LINKED).toBe('LINKED');
    expect(UserState.SYNCING).toBe('SYNCING');
    expect(UserState.ACTIVE).toBe('ACTIVE');
  });

  it('state values are strings (safe to store in MongoDB)', () => {
    for (const value of Object.values(UserState)) {
      expect(typeof value).toBe('string');
    }
  });
});

// State transition logic
function nextState(current: UserState, event: string): UserState {
  switch (current) {
    case UserState.INIT:
      return UserState.QR_SENT;
    case UserState.QR_SENT:
      if (event === 'qr_scanned') return UserState.LINKED;
      return UserState.QR_SENT;
    case UserState.LINKED:
      return UserState.SYNCING;
    case UserState.SYNCING:
      if (event === 'sync_complete') return UserState.ACTIVE;
      return UserState.SYNCING;
    case UserState.ACTIVE:
      return UserState.ACTIVE;
  }
}

describe('state transitions', () => {
  it('INIT -> QR_SENT on first message', () => {
    expect(nextState(UserState.INIT, 'hi')).toBe(UserState.QR_SENT);
  });

  it('QR_SENT -> LINKED on qr_scanned', () => {
    expect(nextState(UserState.QR_SENT, 'qr_scanned')).toBe(UserState.LINKED);
  });

  it('stays QR_SENT on other events', () => {
    expect(nextState(UserState.QR_SENT, 'some text')).toBe(UserState.QR_SENT);
  });

  it('LINKED -> SYNCING immediately', () => {
    expect(nextState(UserState.LINKED, 'any')).toBe(UserState.SYNCING);
  });

  it('SYNCING -> ACTIVE on sync_complete', () => {
    expect(nextState(UserState.SYNCING, 'sync_complete')).toBe(UserState.ACTIVE);
  });

  it('ACTIVE stays ACTIVE', () => {
    expect(nextState(UserState.ACTIVE, 'anything')).toBe(UserState.ACTIVE);
  });
});
