-- T2U supplies a verified SDK tunnel port. It does not expose RTSP itself.
-- Keep RTSP nullable so the dashboard never invents a video endpoint.
ALTER TABLE p2p_sessions
    ALTER COLUMN rtsp_local_port DROP NOT NULL;

