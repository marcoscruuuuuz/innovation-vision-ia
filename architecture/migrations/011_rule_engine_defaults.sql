CREATE OR REPLACE FUNCTION vision_apply_rule_engine_defaults()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    event_name text;
    engine_name text;
BEGIN
    SELECT event_type INTO event_name FROM event_rules WHERE id = NEW.event_rule_id;

    engine_name := CASE event_name
        WHEN 'porta_aberta_bloco' THEN 'door_structural_change_temporal'
        WHEN 'linha_perimetral' THEN 'tracker_temporal'
        WHEN 'cachorro_solto' THEN 'detector_tracker_temporal'
        WHEN 'cachorro_fazendo_fezes' THEN 'detector_pose_temporal_vlm_review'
        WHEN 'entrada_vacuo' THEN 'tracker_temporal'
        WHEN 'saida_vacuo' THEN 'tracker_temporal'
        WHEN 'pessoa_portao_veicular' THEN 'person_tracker'
        WHEN 'crianca_bicicleta_area_comum' THEN 'child_classifier_object_association'
        WHEN 'crianca_correndo_area_comum' THEN 'child_classifier_pose_tracker'
        WHEN 'veiculo_area_proibida' THEN 'vehicle_tracker_temporal'
        WHEN 'veiculo_contramao' THEN 'vehicle_tracker_direction'
        WHEN 'movimentacao_apos_22h' THEN 'motion_scene_change_detector'
        WHEN 'bola_fora_quadra' THEN 'child_person_ball_association'
        WHEN 'crianca_soltando_pipa' THEN 'child_person_kite_temporal'
        WHEN 'placa_detectada' THEN 'vehicle_plate_detector_ocr_temporal_vote'
        WHEN 'face_detectada' THEN 'face_detector'
        WHEN 'porteiro_dormindo' THEN 'person_pose_inactivity'
        WHEN 'porteiro_fora_posto' THEN 'person_absence_temporal'
        WHEN 'lixo_no_chao' THEN 'person_object_abandonment'
        ELSE 'snapshot_detector'
    END;

    NEW.parameters := COALESCE(NEW.parameters, '{}'::jsonb) || jsonb_build_object('engine', engine_name);

    IF engine_name IN (
        'door_structural_change_temporal','tracker_temporal','person_tracker',
        'detector_tracker_temporal','detector_pose_temporal_vlm_review',
        'child_classifier_object_association','child_classifier_pose_tracker','vehicle_tracker_temporal',
        'vehicle_tracker_direction','motion_scene_change_detector','child_person_ball_association',
        'child_person_kite_temporal','vehicle_plate_detector_ocr_temporal_vote','person_pose_inactivity',
        'person_absence_temporal','person_object_abandonment'
    ) THEN
        NEW.parameters := NEW.parameters || jsonb_build_object('requires_temporal', true);
    ELSE
        NEW.parameters := NEW.parameters - 'requires_temporal';
    END IF;

    IF event_name = 'porta_aberta_bloco' THEN
        NEW.parameters := NEW.parameters || jsonb_build_object(
            'open_persistence_seconds', 15,
            'capture_snapshot', true,
            'capture_mini_clip', true
        );
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_rule_engine_defaults ON event_rule_versions;
CREATE TRIGGER trg_rule_engine_defaults
BEFORE INSERT OR UPDATE OF parameters ON event_rule_versions
FOR EACH ROW EXECUTE FUNCTION vision_apply_rule_engine_defaults();

UPDATE event_rule_versions v
SET parameters = v.parameters
FROM event_rules r
WHERE r.id = v.event_rule_id;
