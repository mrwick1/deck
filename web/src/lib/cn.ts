import { createCn } from "cn/config";

/** `cn` with the custom text sizes of the design system. Without them, `text-ui` reads as a color and a later color removes it. */
export const cn = createCn({ extend: { classGroups: { "font-size": [{ text: ["ui", "2xs", "display"] }] } } });
