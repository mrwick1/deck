import { motion } from 'motion/react';

/** The empty chat: a greeting that rises in. The suggestions come from the runtime. */
export function Welcome() {
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ type: 'spring', stiffness: 300, damping: 30 }}
      className="mb-6 flex flex-col gap-1 px-2"
    >
      <p className="text-2xl font-medium tracking-tight">What should deck do?</p>
      <p className="text-muted-foreground text-sm">It works on the web for you and shows each step.</p>
    </motion.div>
  );
}
