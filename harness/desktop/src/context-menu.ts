import type { MenuItemConstructorOptions, ContextMenuParams } from "electron";

/**
 * The native editing menu of the app's own page (desktop #735): right-click on a selection
 * copies it, right-click in a field offers cut, copy, paste and select all. Native editing
 * acts on the person's selection and does not grant page scripts clipboard reads.
 */
export function editContextMenu(params: Pick<ContextMenuParams, "isEditable" | "selectionText" | "editFlags">): MenuItemConstructorOptions[] {
  const { editFlags: flags } = params;
  if (params.isEditable) return [
    { role: "cut", enabled: flags.canCut },
    { role: "copy", enabled: flags.canCopy },
    { role: "paste", enabled: flags.canPaste },
    { type: "separator" },
    { role: "selectAll", enabled: flags.canSelectAll },
  ];
  return params.selectionText ? [{ role: "copy", enabled: flags.canCopy }] : [];
}
