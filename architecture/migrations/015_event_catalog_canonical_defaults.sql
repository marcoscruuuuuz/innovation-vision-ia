-- Canonical event aliases introduced after the initial operational portal migration.
-- Existing rule versions are retained; this only normalizes their safe default engine metadata.
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
        WHEN event_name IN ('porta_aberta_bloco', 'porta_bloco_aberta', 'porta_manutencao') THEN 'door_structural_change_temporal'
        WHEN event_name IN ('linha_perimetral', 'linha_perimetral_cerca_eletrica', 'linha_perimetral_disparo', 'linha_velocidade',
             'entrada_vacuo', 'saida_vacuo') THEN 'tracker_temporal'
        WHEN event_name = 'pessoa_portao_veicular' THEN 'person_tracker'
        WHEN event_name IN ('cachorro_solto', 'animais_pets', 'muro_condominio', 'area_proibida',
             'pessoa_bicicleta_area_comum') THEN 'detector_tracker_temporal'
        WHEN event_name IN ('cachorro_fazendo_fezes', 'morador_nao_recolheu_fezes', 'possiveis_fezes') THEN 'detector_pose_temporal_vlm_review'
        WHEN event_name = 'crianca_bicicleta_area_comum' THEN 'child_classifier_object_association'
        WHEN event_name = 'crianca_correndo_area_comum' THEN 'child_classifier_pose_tracker'
        WHEN event_name IN ('veiculo_area_proibida', 'veiculo_parado_irregular') THEN 'vehicle_tracker_temporal'
        WHEN event_name = 'veiculo_contramao' THEN 'vehicle_tracker_direction'
        WHEN event_name IN ('movimentacao_apos_22h', 'pessoa_fora_horario_22h') THEN 'motion_scene_change_detector'
        WHEN event_name IN ('bola_fora_quadra', 'criancas_jogando_bola') THEN 'child_person_ball_association'
        WHEN event_name IN ('crianca_soltando_pipa', 'crianca_com_pipa') THEN 'child_person_kite_temporal'
        WHEN event_name = 'placa_detectada' THEN 'vehicle_plate_detector_ocr_temporal_vote'
        WHEN event_name = 'face_detectada' THEN 'face_detector'
        WHEN event_name IN ('porteiro_dormindo', 'possivel_porteiro_dormindo') THEN 'person_pose_inactivity'
        WHEN event_name = 'porteiro_fora_posto' THEN 'person_absence_temporal'
        WHEN event_name = 'lixo_no_chao' THEN 'person_object_abandonment'
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

    IF event_name IN ('porta_aberta_bloco', 'porta_bloco_aberta', 'porta_manutencao') THEN
        NEW.parameters := NEW.parameters || jsonb_build_object(
            'open_persistence_seconds', 15,
            'duration_seconds', 15,
            'capture_snapshot', true,
            'capture_mini_clip', true
        );
    ELSIF event_name = 'face_detectada' THEN
        NEW.parameters := NEW.parameters || jsonb_build_object('classes', jsonb_build_array('face'));
    ELSIF event_name IN ('area_janelas_apartamentos', 'pessoa_portao_veicular') THEN
        NEW.parameters := NEW.parameters || jsonb_build_object('classes', jsonb_build_array('person'));
    ELSIF event_name IN ('entrada_vacuo', 'saida_vacuo') THEN
        NEW.parameters := NEW.parameters || jsonb_build_object(
            'classes', jsonb_build_array('car','truck','bus','motorcycle','vehicle'),
            'crossing_order', CASE WHEN event_name='entrada_vacuo' THEN 'forward' ELSE 'reverse' END,
            'vacuum_max_gap_seconds', 3
        );
    ELSIF event_name IN ('veiculo_area_proibida', 'veiculo_parado_irregular') THEN
        NEW.parameters := NEW.parameters || jsonb_build_object(
            'classes', jsonb_build_array('car','truck','bus','motorcycle','vehicle'),
            'duration_seconds', 20,
            'max_stationary_displacement', 0.04
        );
    ELSIF event_name = 'veiculo_contramao' THEN
        NEW.parameters := NEW.parameters || jsonb_build_object(
            'classes', jsonb_build_array('car','truck','bus','motorcycle','vehicle')
        );
    ELSIF event_name IN ('movimentacao_apos_22h', 'pessoa_fora_horario_22h') THEN
        NEW.parameters := NEW.parameters || jsonb_build_object(
            'schedule', '22:00-06:00',
            'timezone', 'America/Sao_Paulo'
        );
    END IF;

    RETURN NEW;
END;
$$;

UPDATE event_rule_versions
SET parameters = parameters;
