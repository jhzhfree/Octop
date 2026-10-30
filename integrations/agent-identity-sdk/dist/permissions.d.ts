/**
 * Filesystem protection for identity material.
 *
 * POSIX enforces account ownership plus 0600/0700 mode bits. Windows has
 * neither, so it enforces the equivalent owner-only ACL through icacls. A host
 * that can enforce neither is refused instead of keeping keys in the open.
 *
 * Windows equivalence notes: the Administrators group and SYSTEM can override
 * file ACLs by privilege (as root can on POSIX), so they are not treated as
 * "other users" for directories. Private key files stay stricter: only the
 * owning account may appear in their ACL.
 */
import { type Stats } from "node:fs";
export type ProtectedKind = "file" | "directory";
export interface ProtectionPolicy {
    readonly platform: "posix" | "windows";
    /** Restrict a freshly created file or directory to the owning account. */
    harden(target: string, kind: ProtectedKind): Promise<void>;
    /** Flush the containing directory so an atomic rename is durable. */
    syncDirectory(directory: string): Promise<void>;
    /** Check an already opened file; POSIX re-checks the handle to avoid TOCTOU. */
    verifyHandle(info: Stats): void;
    /** Check a file before secrets are read from it. */
    verifyFile(target: string): Promise<void>;
    /** Check the key store directory itself. */
    verifyDirectory(target: string): Promise<void>;
    /** Check every ancestor directory is not writable by other accounts. */
    verifyParents(target: string): Promise<void>;
}
export declare function protectionPolicy(): ProtectionPolicy;
/** Whether this host can enforce owner-only protection at all. */
export declare function protectionSupported(): boolean;
