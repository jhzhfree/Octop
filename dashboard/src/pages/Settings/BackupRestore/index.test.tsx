import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { App } from "antd";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import BackupRestorePanel from "./index";
const state = vi.hoisted(() => ({
  mobile: false,
  t: (key: string) => key,
  restore: vi.fn(),
  download: vi.fn().mockResolvedValue(new Blob(["synthetic backup"])),
  save: vi.fn(),
  op: {
    kind: null,
    restoreTarget: null,
    uploadPercent: null,
    busy: false,
    creating: false,
    restoring: false,
    autoRunning: false,
    createBackup: vi.fn(),
    runAutoBackup: vi.fn(),
    uploadBackup: vi.fn(),
    syncFromServer: vi.fn(),
    setOnSettled: vi.fn(),
  },
}));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: state.t }) }));
vi.mock("../../../hooks/useIsMobile", () => ({
  useIsMobile: () => state.mobile,
}));
vi.mock("../../../hooks/useServerTimezone", () => ({
  useServerTimezone: () => "UTC",
}));
vi.mock("../../../context/ServiceRestartContext", () => ({
  useServiceRestartContext: () => ({ isRestarting: false }),
}));
vi.mock("../../../context/BackupOperationContext", () => ({
  useBackupOperation: () => ({ ...state.op, restoreBackup: state.restore }),
}));
vi.mock("../../../utils/saveBlobAsFile", () => ({
  saveBlobAsFile: state.save,
}));
vi.mock("../../../api/modules/backup", () => ({
  backupApi: {
    listBackups: vi.fn().mockResolvedValue({
      dir: "synthetic-backups",
      items: [
        {
          name: "synthetic.tar.gz",
          size: 10,
          created_at: "2026-11-15T00:00:00Z",
          modified_at: "2026-11-15T00:00:00Z",
        },
      ],
    }),
    getAutoSettings: vi.fn().mockResolvedValue({
      auto_enabled: false,
      schedule: "cron:0 4 * * *",
      retention_count: 7,
    }),
    downloadBackup: state.download,
  },
}));
beforeEach(() => {
  vi.clearAllMocks();
});
afterEach(cleanup);
it.each([false, true])(
  "blocks restore but leaves backup download/create usable (mobile=%s)",
  async (mobile) => {
    state.mobile = mobile;
    render(
      <App>
        <BackupRestorePanel />
      </App>,
    );
    await screen.findByText("synthetic.tar.gz");
    expect(
      screen.getByText("apiErrors.BACKUP_ONLINE_RESTORE_UNAVAILABLE"),
    ).toBeInTheDocument();
    const restore = screen.getByRole("button", {
      name: "backup.restoreAction",
    });
    expect(restore).toBeDisabled();
    fireEvent.click(restore);
    expect(state.restore).not.toHaveBeenCalled();
    const download = screen.getByRole("button", { name: "common.download" });
    expect(download).toBeEnabled();
    fireEvent.click(download);
    await waitFor(() => expect(state.save).toHaveBeenCalled());
    expect(state.download).toHaveBeenCalledWith("synthetic.tar.gz");
    const create = screen.getByRole("button", { name: "backup.createButton" });
    expect(create).toBeEnabled();
    fireEvent.click(create);
    await screen.findByText("backup.createModalDesc");
  },
);
