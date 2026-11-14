import { useState } from "react";
import { Drawer } from "vaul";
import { Check, ChevronDown, X } from "lucide-react";
import { Button } from "../components/ui/button";
import "./choice-sheet.css";

/** A touch-sized choice list with modal focus handling and a retained trigger. */
export function ChoiceSheet({ label, value, options, onChange, disabled = false }: {
  label: string; value: string; options: { value: string; label: string; disabled?: boolean }[];
  onChange: (value: string) => void; disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  return <Drawer.Root autoFocus open={open && !disabled} onOpenChange={setOpen} repositionInputs={false}>
    <Drawer.Trigger asChild><Button type="button" variant="ghost" className="choice-trigger" disabled={disabled} aria-label={`${label}: ${options.find(option => option.value === value)?.label ?? "Choose"}`}><span>{options.find(option => option.value === value)?.label ?? "Choose"}</span><ChevronDown size={16}/></Button></Drawer.Trigger>
    <Drawer.Portal><Drawer.Overlay className="choice-overlay"/><Drawer.Content className="choice-sheet"><Drawer.Handle className="choice-handle"/><div className="choice-heading"><Drawer.Title>{label}</Drawer.Title><Drawer.Close asChild><Button variant="ghost" size="icon" aria-label="Close choices"><X size={20}/></Button></Drawer.Close></div><Drawer.Description className="choice-description">Choose where this instruction goes.</Drawer.Description><div className="choice-options">{options.map(option => <Button key={option.value} variant="ghost" className="choice-option" aria-pressed={option.value === value} disabled={option.disabled} onClick={() => { onChange(option.value); setOpen(false); }}><span>{option.label}</span>{value === option.value && <Check size={20}/>}</Button>)}</div></Drawer.Content></Drawer.Portal>
  </Drawer.Root>;
}
