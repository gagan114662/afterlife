import { program } from 'commander';
import { runSync } from './sync';
import { runBot } from './bot';

program
  .option('--mode <mode>', 'Run mode: sync | bot', 'bot')
  .parse(process.argv);

const opts = program.opts();

async function main() {
  const mode = opts.mode as string;
  if (mode === 'sync') {
    console.log('[afterlife] Starting WhatsApp sync...');
    await runSync();
  } else if (mode === 'bot') {
    console.log('[afterlife] Starting WhatsApp bot...');
    await runBot();
  } else {
    console.error(`Unknown mode: ${mode}. Use --mode sync or --mode bot`);
    process.exit(1);
  }
}

main().catch((err) => {
  console.error('[afterlife] Fatal error:', err);
  process.exit(1);
});
