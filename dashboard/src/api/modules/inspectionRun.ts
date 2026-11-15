import { request } from "../request";

export interface InspectionJobOption {
  job_id: string;
  name: string;
  job_version: number;
  connection_version: number;
}
export const inspectionRunApi = {
  candidates: (organizationId: string, sceneId: string) =>
    request<InspectionJobOption[]>(
      `/organizations/${encodeURIComponent(
        organizationId,
      )}/scenes/${encodeURIComponent(sceneId)}/inspection-jobs`,
      { cache: "no-store" },
    ),
};
