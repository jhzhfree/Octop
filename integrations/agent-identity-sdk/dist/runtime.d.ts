import type { AgentIdentityRuntime, RuntimeDependencies } from "./types.js";
export type * from "./types.js";
export { IdentityError } from "./core.js";
export declare function createRuntime<NativeContext>(dependencies: RuntimeDependencies<NativeContext>): AgentIdentityRuntime<NativeContext>;
