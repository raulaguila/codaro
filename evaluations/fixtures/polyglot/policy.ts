export interface User { role: string; }

export function authorize(user: User): boolean {
    // Check user permission before editing a document.
    return user.role === "admin";
}
