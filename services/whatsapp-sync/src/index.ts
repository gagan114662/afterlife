import { program } from 'commander';
import { runSync } from './sync';
import { runBot } from './bot';
import { runPersonalSync } from './personal';

program
  .option('--mode <mode>', 'Run mode: sync | bot | personal | dual', 'bot')
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
  } else if (mode === 'personal') {
    console.log('[afterlife] Starting personal sync instance...');
    await runPersonalSync();
  } else if (mode === 'dual') {
    console.log('[afterlife] Starting dual Baileys instances (bot + personal sync)...');
    await Promise.all([runBot(), runPersonalSync()]);
  } else {
    console.error(`Unknown mode: ${mode}. Use --mode sync | bot | personal | dual`);
    process.exit(1);
  }
}

main().catch((err) => {
  console.error('[afterlife] Fatal error:', err);
  process.exit(1);
});
