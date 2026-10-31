/** Escape spreadsheet cells without allowing a user-provided name to become a formula. */
export function csvCell(raw:string){const value=/^[\s]*[=+@\-]/.test(raw)?`'${raw}`:raw;return `"${value.replaceAll('"','""')}"`}
