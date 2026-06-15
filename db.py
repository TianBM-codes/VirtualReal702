import threading

import mysql.connector
import mysql.connector.pooling

try:
    from VirtualReal702.config import DB_CONFIG
except ImportError:  # pragma: no cover - local direct run fallback
    from config import DB_CONFIG

CREATE_TABLE_SQL_LIST = [
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_console_log (
        pid BIGINT NOT NULL COMMENT '宸ョ▼ID',
        `time` BIGINT NOT NULL COMMENT '鏃ュ織鏃堕棿鎴?姣)',
        log_text VARCHAR(2048) NOT NULL COMMENT 'HTML鏍煎紡鏃ュ織鍐呭',
        KEY idx_pid_time (pid, `time`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='鎺у埗鍙版棩蹇楄〃';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_test_node (
        nid VARCHAR(100) NOT NULL COMMENT '涓婚敭ID',
        pid BIGINT NOT NULL COMMENT '宸ョ▼ID',
        fid BIGINT NOT NULL COMMENT '鏂囦欢ID',
        ics INT NOT NULL COMMENT '杈撳叆鍧愭爣绯?,
        ocs INT NOT NULL COMMENT '杈撳嚭鍧愭爣绯?,
        x DOUBLE NOT NULL COMMENT 'X鍧愭爣鍊?,
        y DOUBLE NOT NULL COMMENT 'Y鍧愭爣鍊?,
        z DOUBLE NOT NULL COMMENT 'Z鍧愭爣鍊?,
        PRIMARY KEY (nid, pid, fid)
    ) COMMENT='妯℃€佽瘯楠屾祴鐐瑰潗鏍囪〃'
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_test_element (
        element_no INT NOT NULL COMMENT '鍗曞厓缂栧彿',
        pid BIGINT NOT NULL COMMENT '宸ョ▼ID',
        element_type VARCHAR(50) NOT NULL COMMENT '鍗曞厓绫诲瀷',
        point1 INT NULL COMMENT '鑺傜偣1缂栧彿',
        point2 INT NULL COMMENT '鑺傜偣2缂栧彿',
        point3 INT NULL COMMENT '鑺傜偣3缂栧彿',
        point4 INT NULL COMMENT '鑺傜偣4缂栧彿',
        PRIMARY KEY (element_no, pid)
    ) COMMENT='妯℃€佽瘯楠屾祴璇曞崟鍏冧俊鎭〃'
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_test_modal_frequency (
        mode_no INT NOT NULL COMMENT '鎸瀷缂栧彿',
        pid BIGINT NOT NULL COMMENT '宸ョ▼ID',
        fid BIGINT NOT NULL COMMENT '鏂囦欢ID',
        frequency DOUBLE NOT NULL COMMENT '棰戠巼鍊?,
        damping DOUBLE NOT NULL COMMENT '闃诲凹姣?,
        eigenvalue_Re DOUBLE COMMENT '鐗瑰緛鍊煎疄閮?,
        eigenvalue_Im DOUBLE COMMENT '鐗瑰緛鍊艰櫄閮?,
        PRIMARY KEY (mode_no, pid, fid)
    ) COMMENT='妯℃€佽瘯楠岄鐜囨暟鎹〃'
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_test_modal_shape (
        mode_no INT NOT NULL COMMENT '鎸瀷缂栧彿',
        pid BIGINT NOT NULL COMMENT '宸ョ▼ID',
        modal_shape JSON NOT NULL COMMENT '鎸瀷鏁版嵁',
        PRIMARY KEY (mode_no, pid)
    ) COMMENT='璇曢獙鎸姩妯℃€佹暟鎹〃'
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_test_modal_shape_imag (
        mode_no INT NOT NULL COMMENT '鎸瀷缂栧彿',
        pid BIGINT NOT NULL COMMENT '宸ョ▼ID',
        point INT NOT NULL COMMENT '鑺傜偣ID',
        re_ux DOUBLE COMMENT 'x鏂瑰悜瀹為儴',
        im_ux DOUBLE COMMENT 'x鏂瑰悜铏氶儴',
        re_uy DOUBLE COMMENT 'y鏂瑰悜瀹為儴',
        im_uy DOUBLE COMMENT 'y鏂瑰悜铏氶儴',
        re_uz DOUBLE COMMENT 'z鏂瑰悜瀹為儴',
        im_uz DOUBLE COMMENT 'z鏂瑰悜铏氶儴',
        PRIMARY KEY (mode_no, pid, point)
    ) COMMENT='璇曢獙鎸姩妯℃€佹暟鎹〃--澶嶆ā鎬佽妭鐐规尟鍨?
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_test_modal_shape_real (
        mode_no INT NOT NULL COMMENT '鎸瀷缂栧彿',
        pid BIGINT NOT NULL COMMENT '宸ョ▼ID',
        point INT NOT NULL COMMENT '鑺傜偣ID',
        ux DOUBLE COMMENT 'x鏂瑰悜浣嶇Щ鍒嗛噺',
        uy DOUBLE COMMENT 'y鏂瑰悜浣嶇Щ鍒嗛噺',
        uz DOUBLE COMMENT 'z鏂瑰悜浣嶇Щ鍒嗛噺',
        PRIMARY KEY (mode_no, pid, point)
    ) COMMENT='璇曢獙鎸姩妯℃€佹暟鎹〃--澶嶆ā鎬佽妭鐐规尟鍨?
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_test_static_result (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '涓婚敭ID',
        pid BIGINT NOT NULL COMMENT '宸ョ▼ID',
        fid BIGINT NOT NULL COMMENT '鏂囦欢ID',
        load_case_no INT NOT NULL COMMENT '杞借嵎宸ュ喌鍙?,
        result_no INT NOT NULL COMMENT '缁撴灉搴忓彿',
        point INT NOT NULL COMMENT '娴嬬偣ID',
        ux DOUBLE NULL COMMENT 'X鏂瑰悜浣嶇Щ',
        uy DOUBLE NULL COMMENT 'Y鏂瑰悜浣嶇Щ',
        uz DOUBLE NULL COMMENT 'Z鏂瑰悜浣嶇Щ',
        rx DOUBLE NULL COMMENT 'X鏂瑰悜杞',
        ry DOUBLE NULL COMMENT 'Y鏂瑰悜杞',
        rz DOUBLE NULL COMMENT 'Z鏂瑰悜杞',
        load_factor DOUBLE NULL COMMENT '杞借嵎鍥犲瓙',
        extra_json JSON NULL COMMENT '鎵╁睍淇℃伅',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '鍒涘缓鏃堕棿',
        PRIMARY KEY (id),
        UNIQUE KEY uk_pid_fid_case_result_point (pid, fid, load_case_no, result_no, point),
        KEY idx_pid_case_result (pid, load_case_no, result_no),
        KEY idx_pid_point (pid, point)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='璇曢獙闈欏姏缁撴灉琛?;
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_test_coord (
        id INT NOT NULL AUTO_INCREMENT COMMENT '涓婚敭ID',
        pid BIGINT NOT NULL COMMENT '宸ョ▼ID',
        coord_no INT NOT NULL COMMENT '鍧愭爣缂栧彿',
        ref_coord_no INT NOT NULL COMMENT '鍙傝€冨潗鏍囩紪鍙?,
        PRIMARY KEY (id, pid)
    ) COMMENT='娴嬭瘯鍧愭爣淇℃伅琛?
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_coord (
        id INT NOT NULL AUTO_INCREMENT COMMENT '涓婚敭ID',
        pid BIGINT NOT NULL COMMENT '宸ョ▼ID',
        fid BIGINT NOT NULL COMMENT '鏂囦欢ID',
        coord_no INT NOT NULL COMMENT '鍧愭爣缂栧彿',
        ref_coord_no INT NOT NULL COMMENT '鍙傝€冨潗鏍囩紪鍙?,
        coord_type VARCHAR(32) NOT NULL COMMENT '鍧愭爣绫诲瀷',
        x1 DOUBLE NOT NULL COMMENT '鍧愭爣鍒嗛噺X1',
        x2 DOUBLE NOT NULL COMMENT '鍧愭爣鍒嗛噺X2',
        x3 DOUBLE NOT NULL COMMENT '鍧愭爣鍒嗛噺X3',
        x4 DOUBLE NOT NULL COMMENT '鍧愭爣鍒嗛噺X4',
        x5 DOUBLE NOT NULL COMMENT '鍧愭爣鍒嗛噺X5',
        x6 DOUBLE NOT NULL COMMENT '鍧愭爣鍒嗛噺X6',
        x7 DOUBLE NOT NULL COMMENT '鍧愭爣鍒嗛噺X7',
        x8 DOUBLE NOT NULL COMMENT '鍧愭爣鍒嗛噺X8',
        x9 DOUBLE NOT NULL COMMENT '鍧愭爣鍒嗛噺X9',
        PRIMARY KEY (id, pid, fid)
    ) COMMENT='鏈夐檺鍏冨潗鏍囦俊鎭〃'
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_material_overview (
        Id INT NOT NULL COMMENT '涓婚敭ID',
        pid BIGINT NOT NULL COMMENT '宸ョ▼ID',
        Type VARCHAR(32) NOT NULL COMMENT '鏉愭枡鍚嶇О',
        PRIMARY KEY (Id, pid)
    ) COMMENT='鏈夐檺鍏冩ā鍨嬫潗鏂欐€昏琛?
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_isotropic (
        Id INT NOT NULL COMMENT '涓婚敭ID',
        pid BIGINT NOT NULL COMMENT '宸ョ▼ID',
        RHO DOUBLE NOT NULL COMMENT '瀵嗗害',
        E DOUBLE NOT NULL COMMENT '寮规€фā閲?,
        NU DOUBLE NOT NULL COMMENT '娉婃澗姣?,
        GE DOUBLE NOT NULL COMMENT '鏉愭枡闃诲凹',
        PRIMARY KEY (Id, pid)
    ) COMMENT='鏈夐檺鍏冩ā鍨嬪悇鍚戝悓鎬ф潗鏂欒〃'
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_ortho2d (
        Id INT NOT NULL COMMENT 'primary key',
        pid BIGINT NOT NULL COMMENT 'project id',
        RHO DOUBLE NULL COMMENT '瀵嗗害',
        EX DOUBLE NULL COMMENT '寮规€фā閲弜鏂瑰悜',
        EY DOUBLE NULL COMMENT '寮规€фā閲弝鏂瑰悜',
        GXY DOUBLE NULL COMMENT '鍓垏妯￠噺XY',
        NUXY DOUBLE NULL COMMENT '娉婃澗姣擷Y',
        GXZ DOUBLE NULL COMMENT '鍓垏妯￠噺XZ',
        GYZ DOUBLE NULL COMMENT '鍓垏妯￠噺YZ',
        GE DOUBLE NULL COMMENT '鏉愭枡闃诲凹',
        PRIMARY KEY (Id, pid)
    ) COMMENT='FEM orthotropic 2D materials'
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_aniso3d (
        Id INT NOT NULL COMMENT 'primary key',
        pid BIGINT NOT NULL COMMENT 'project id',
        RHO DOUBLE NULL COMMENT '瀵嗗害',
        D11 DOUBLE NULL COMMENT 'D11',
        D12 DOUBLE NULL COMMENT 'D12',
        D13 DOUBLE NULL COMMENT 'D13',
        D14 DOUBLE NULL COMMENT 'D14',
        D15 DOUBLE NULL COMMENT 'D15',
        D16 DOUBLE NULL COMMENT 'D16',
        D22 DOUBLE NULL COMMENT 'D22',
        D23 DOUBLE NULL COMMENT 'D23',
        D24 DOUBLE NULL COMMENT 'D24',
        D25 DOUBLE NULL COMMENT 'D25',
        D26 DOUBLE NULL COMMENT 'D26',
        D33 DOUBLE NULL COMMENT 'D33',
        D34 DOUBLE NULL COMMENT 'D34',
        D35 DOUBLE NULL COMMENT 'D35',
        D36 DOUBLE NULL COMMENT 'D36',
        D44 DOUBLE NULL COMMENT 'D44',
        D45 DOUBLE NULL COMMENT 'D45',
        D46 DOUBLE NULL COMMENT 'D46',
        D55 DOUBLE NULL COMMENT 'D55',
        D56 DOUBLE NULL COMMENT 'D56',
        D66 DOUBLE NULL COMMENT 'D66',
        GE DOUBLE NULL COMMENT '鏉愭枡闃诲凹',
        PRIMARY KEY (Id, pid)
    ) COMMENT='FEM anisotropic 3D materials'
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_property (
        Id INT NOT NULL COMMENT '涓婚敭ID',
        pid BIGINT NOT NULL COMMENT '宸ョ▼ID',
        Type VARCHAR(32) NOT NULL COMMENT '灞炴€у悕绉?,
        PRIMARY KEY (Id, pid)
    ) COMMENT='鏈夐檺鍏冩ā鍨嬪睘鎬ц〃'
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_shell_property (
        Id INT NOT NULL COMMENT '涓婚敭ID',
        pid BIGINT NOT NULL COMMENT '宸ョ▼ID',
        Thickness DOUBLE NOT NULL COMMENT '鍘氬害',
        NSM DOUBLE NOT NULL COMMENT '闈炵粨鏋勮川閲?,
        THETA DOUBLE NOT NULL COMMENT '鏃嬭浆瑙?,
        element_set VARCHAR(255) NULL COMMENT '鍗曞厓闆嗗悕绉?,
        PRIMARY KEY (Id, pid)
    ) COMMENT='鏈夐檺鍏冩ā鍨嬪３鍗曞厓灞炴€ц〃'
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_beam_property (
        Id INT NOT NULL COMMENT '涓婚敭ID',
        pid BIGINT NOT NULL COMMENT '宸ョ▼ID',
        AX DOUBLE NOT NULL COMMENT '鎴潰绉?,
        AY DOUBLE NOT NULL COMMENT 'Y鍚戝壀鍒囧彉褰㈢缉鍑忔埅闈?,
        AZ DOUBLE NOT NULL COMMENT 'Z鍚戝壀鍒囧彉褰㈢缉鍑忔埅闈?,
        IX DOUBLE NOT NULL COMMENT '鎶楁壄鎯€х煩',
        IY DOUBLE NOT NULL COMMENT 'Y杞存儻鎬х煩',
        IZ DOUBLE NOT NULL COMMENT 'Z杞存儻鎬х煩',
        CW DOUBLE NOT NULL COMMENT '缈樻洸绯绘暟',
        YN DOUBLE NOT NULL COMMENT '涓€ц酱Y鍚戝潗鏍?,
        ZN DOUBLE NOT NULL COMMENT '涓€ц酱Z鍚戝潗鏍?,
        NSM DOUBLE NOT NULL COMMENT '闈炵粨鏋勮川閲?,
        PRIMARY KEY (Id, pid)
    ) COMMENT='鏈夐檺鍏冩ā鍨嬫鍗曞厓灞炴€ц〃'
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_solid_property (
        Id INT NOT NULL COMMENT '涓婚敭ID',
        pid BIGINT NOT NULL COMMENT '宸ョ▼ID',
        MID INT NULL COMMENT '鏉愭枡ID',
        CID INT NULL COMMENT '鍧愭爣绯籌D',
        PRIMARY KEY (Id, pid)
    ) COMMENT='FEM solid properties'
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_layered_property (
        Id INT NOT NULL COMMENT 'primary key',
        pid BIGINT NOT NULL COMMENT 'project id',
        Offset_L DOUBLE NULL COMMENT 'Offset',
        Theta DOUBLE NULL COMMENT 'Theta',
        GE DOUBLE NULL COMMENT 'GE',
        NSM DOUBLE NULL COMMENT 'NSM',
        Layers INT NULL COMMENT 'Layers',
        PRIMARY KEY (Id, pid)
    ) COMMENT='FEM layered properties'
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_boundary (
        Id INT NOT NULL COMMENT '涓婚敭ID',
        pid BIGINT NOT NULL COMMENT '宸ョ▼ID',
        Node BIGINT NOT NULL COMMENT '鑺傜偣鍙?,
        UX DOUBLE COMMENT 'X鏂瑰悜浣嶇Щ绾︽潫, 鎸囧畾浣嶇Щ',
        UY DOUBLE COMMENT 'Y鏂瑰悜浣嶇Щ绾︽潫, 鎸囧畾浣嶇Щ',
        UZ DOUBLE COMMENT 'Z鏂瑰悜浣嶇Щ绾︽潫, 鎸囧畾浣嶇Щ',
        RX DOUBLE COMMENT 'X鏂瑰悜鏃嬭浆绾︽潫, 鎸囧畾浣嶇Щ',
        RY DOUBLE COMMENT 'Y鏂瑰悜鏃嬭浆绾︽潫, 鎸囧畾浣嶇Щ',
        RZ DOUBLE COMMENT 'Z鏂瑰悜鏃嬭浆绾︽潫, 鎸囧畾浣嶇Щ',
        PRIMARY KEY (Id, pid)
    ) COMMENT='妯″瀷绾︽潫鏉′欢'
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_node_pairs (
        pid INT NOT NULL COMMENT '宸ョ▼ID',
        node INT NOT NULL COMMENT '鑺傜偣缂栧彿',
        point INT NOT NULL COMMENT '鐐圭紪鍙?,
        distance FLOAT NOT NULL COMMENT '涓ょ偣璺濈',
        x_offset FLOAT NOT NULL COMMENT 'X鏂瑰悜鍋忕Щ閲?,
        y_offset FLOAT NOT NULL COMMENT 'Y鏂瑰悜鍋忕Щ閲?,
        z_offset FLOAT NOT NULL COMMENT 'Z鏂瑰悜鍋忕Щ閲?,
        PRIMARY KEY (pid, node, point)
    ) COMMENT='鏈夐檺鍏冭妭鐐归厤瀵逛俊鎭〃'
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_transform_operation (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '涓婚敭ID',
        pid BIGINT NOT NULL COMMENT '宸ョ▼ID',
        transform_type VARCHAR(32) NOT NULL COMMENT '鍙樻崲绫诲瀷锛堟湁闄愬厓/璇曢獙锛?,
        matrix4_json JSON NOT NULL COMMENT '4x4鐭╅樀鏁版嵁',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '鍒涘缓鏃堕棿',
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '鏇存柊鏃堕棿',
        PRIMARY KEY (id),
        UNIQUE KEY uk_pid_transform_type (pid, transform_type),
        KEY idx_pid_updated_at (pid, updated_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='绌洪棿鍖归厤鍙樻崲璁板綍琛?;
    """,
    """
    -- project configuration table
    -- test_model_x/y/z: test model size
    -- fem_model_x/y/z: fem model size
    -- coefficients_json: coefficient map
    -- extra_json: extra config payload
    CREATE TABLE IF NOT EXISTS t_mt_py_project_config (
        pid BIGINT NOT NULL COMMENT 'project id',
        test_model_x DOUBLE NULL COMMENT '璇曢獙妯″瀷x鏂瑰悜灏哄',
        test_model_y DOUBLE NULL COMMENT '璇曢獙妯″瀷y鏂瑰悜灏哄',
        test_model_z DOUBLE NULL COMMENT '璇曢獙妯″瀷z鏂瑰悜灏哄',
        fem_model_x DOUBLE NULL COMMENT '鏈夐檺鍏冩ā鍨媥鏂瑰悜灏哄',
        fem_model_y DOUBLE NULL COMMENT '鏈夐檺鍏冩ā鍨媦鏂瑰悜灏哄',
        fem_model_z DOUBLE NULL COMMENT '鏈夐檺鍏冩ā鍨媧鏂瑰悜灏哄',
        coefficients_json JSON NULL COMMENT '绯绘暟瀵瑰簲琛?,
        extra_json JSON NULL COMMENT '棰濆绯绘暟瀵瑰簲琛?,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'created time',
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT 'updated time',
        PRIMARY KEY (pid)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='project configuration';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_dof_pairs (
        pid INT NOT NULL COMMENT '宸ョ▼ID',
        node INT NOT NULL COMMENT '鑺傜偣缂栧彿',
        point INT NOT NULL COMMENT '鐐圭紪鍙?,
        dof VARCHAR(32) NOT NULL COMMENT '鑷敱搴︾被鍨?,
        cx FLOAT NOT NULL COMMENT 'X鏂瑰悜鍒嗛噺',
        cy FLOAT NOT NULL COMMENT 'Y鏂瑰悜鍒嗛噺',
        cz FLOAT NOT NULL COMMENT 'Z鏂瑰悜鍒嗛噺',
        PRIMARY KEY (pid, node, point)
    ) COMMENT='鏈夐檺鍏冭嚜鐢卞害閰嶅淇℃伅琛?
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_static_shape_pairs (
        pid INT NOT NULL COMMENT '宸ョ▼ID',
        fem_res VARCHAR(32) NOT NULL COMMENT '鏈夐檺鍏冭绠楃粨鏋?,
        test_res VARCHAR(32) NOT NULL COMMENT '璇曢獙娴嬭瘯缁撴灉',
        DAC FLOAT NOT NULL COMMENT 'DAC(%)',
        DSF FLOAT NOT NULL COMMENT 'DSF',
        PRIMARY KEY (pid)
    ) COMMENT='鏈夐檺鍏冮潤鍔涙尟鍨嬮厤瀵硅〃'
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_supported_quantity (
        quantity_code VARCHAR(32) NOT NULL COMMENT '淇閲忕紪鐮?,
        quantity_name VARCHAR(200) NOT NULL COMMENT '淇閲忓悕绉?,
        unit VARCHAR(50) NULL COMMENT '鍗曚綅',
        enabled TINYINT(1) NOT NULL DEFAULT 1 COMMENT '鏄惁鍚敤',
        sort_no INT NOT NULL DEFAULT 0 COMMENT '鎺掑簭鍙?,
        PRIMARY KEY (quantity_code)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='鏀寔鐨勪慨姝ｉ噺琛?;
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_quantity_set_capability (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '涓婚敭ID',
        pid BIGINT NOT NULL COMMENT '宸ョ▼ID',
        quantity_code VARCHAR(32) NOT NULL COMMENT '淇閲忕紪鐮?,
        set_name VARCHAR(200) NOT NULL COMMENT '闆嗗悎鍚嶇О',
        set_type VARCHAR(32) NOT NULL COMMENT '闆嗗悎绫诲瀷',
        set_scope VARCHAR(32) NOT NULL COMMENT '闆嗗悎鑼冨洿',
        instance_name VARCHAR(200) NULL COMMENT '瀹炰緥鍚嶇О',
        part_name VARCHAR(200) NULL COMMENT '闆朵欢鍚嶇О',
        set_role VARCHAR(64) NOT NULL COMMENT '闆嗗悎瑙掕壊',
        element_family VARCHAR(32) NULL COMMENT '鍗曞厓鏃?,
        section_type VARCHAR(64) NULL COMMENT '鎴潰绫诲瀷',
        material_name VARCHAR(200) NULL COMMENT '鏉愭枡鍚嶇О',
        member_count INT NOT NULL DEFAULT 0 COMMENT '鎴愬憳鏁伴噺',
        supports_global TINYINT(1) NOT NULL DEFAULT 0 COMMENT '鏄惁鏀寔鍏ㄥ眬鍙傛暟',
        supports_local TINYINT(1) NOT NULL DEFAULT 0 COMMENT '鏄惁鏀寔灞€閮ㄥ弬鏁?,
        current_value DOUBLE NULL COMMENT '鍏变韩褰撳墠鍊?,
        is_internal TINYINT(1) NOT NULL DEFAULT 0 COMMENT '鏄惁涓篈baqus鑷姩鐢熸垚鐨勫唴閮ㄩ泦鍚?_PickedSetNN)锛?=鍐呴儴 0=鐢ㄦ埛',
        extra_json JSON NULL COMMENT '鎵╁睍淇℃伅',
        PRIMARY KEY (id),
        UNIQUE KEY uk_pid_quantity_set_capability (pid, quantity_code, set_name, set_type, set_scope, instance_name, part_name),
        KEY idx_pid_quantity_code (pid, quantity_code),
        KEY idx_pid_set_name (pid, set_name, set_type, set_scope)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='淇閲忎笌闆嗗悎鑳藉姏鐩綍琛?;
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_selected_parameter (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '涓婚敭ID',
        pid BIGINT NOT NULL COMMENT '宸ョ▼ID',
        parameter_group_name VARCHAR(200) NOT NULL COMMENT '鍙傛暟缁勫悕绉?,
        parameter_name VARCHAR(200) NOT NULL COMMENT '鍙傛暟鍚嶇О',
        quantity_code VARCHAR(32) NOT NULL COMMENT '淇閲忕紪鐮?,
        selection_mode VARCHAR(32) NOT NULL COMMENT '閫夋嫨妯″紡',
        set_name VARCHAR(200) NOT NULL COMMENT '闆嗗悎鍚嶇О',
        set_type VARCHAR(32) NOT NULL COMMENT '闆嗗悎绫诲瀷',
        set_scope VARCHAR(32) NOT NULL COMMENT '闆嗗悎鑼冨洿',
        instance_name VARCHAR(200) NULL COMMENT '瀹炰緥鍚嶇О',
        part_name VARCHAR(200) NULL COMMENT '闆朵欢鍚嶇О',
        element_label BIGINT NULL COMMENT '鍗曞厓鏍囩',
        current_value DOUBLE NULL COMMENT '褰撳墠鍊?,
        lower DOUBLE NOT NULL DEFAULT 0 COMMENT '涓嬬晫',
        upper DOUBLE NOT NULL DEFAULT 0 COMMENT '涓婄晫',
        prob_id BIGINT NOT NULL DEFAULT 0 COMMENT '闂ID',
        scatter FLOAT NOT NULL DEFAULT 0.25 COMMENT '绂绘暎搴?,
        description VARCHAR(255) NOT NULL DEFAULT '' COMMENT '鎻忚堪',
        extra_json JSON NULL COMMENT '鎵╁睍淇℃伅',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '鍒涘缓鏃堕棿',
        PRIMARY KEY (id),
        UNIQUE KEY uk_pid_parameter_name (pid, parameter_name),
        KEY idx_pid_group_name (pid, parameter_group_name),
        KEY idx_pid_quantity_mode (pid, quantity_code, selection_mode)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='宸查€変慨姝ｅ弬鏁拌〃';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_sol200_parameter_config (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '涓婚敭ID',
        pid BIGINT NOT NULL COMMENT '宸ョ▼ID',
        parameter_no INT NOT NULL COMMENT '鍙傛暟搴忓彿',
        parameter_name VARCHAR(200) NOT NULL COMMENT '鍙傛暟鍚嶇О',
        parameter_type VARCHAR(32) NOT NULL COMMENT '鍙傛暟绫诲瀷',
        property_id BIGINT NULL COMMENT '灞炴€D',
        material_id BIGINT NULL COMMENT '鏉愭枡ID',
        element_id BIGINT NULL COMMENT '鍗曞厓ID',
        initial_value DOUBLE NOT NULL COMMENT '鍒濆鍊?,
        lower_bound DOUBLE NULL COMMENT '涓嬬晫',
        upper_bound DOUBLE NULL COMMENT '涓婄晫',
        extra_json JSON NULL COMMENT '鎵╁睍淇℃伅',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '鍒涘缓鏃堕棿',
        PRIMARY KEY (id),
        UNIQUE KEY uk_pid_sol200_parameter_name (pid, parameter_name),
        UNIQUE KEY uk_pid_sol200_parameter_no (pid, parameter_no),
        KEY idx_pid_sol200_parameter_type (pid, parameter_type)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='SOL200 鍙傛暟閰嶇疆琛?;
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_sol200_response_config (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '涓婚敭ID',
        pid BIGINT NOT NULL COMMENT '宸ョ▼ID',
        response_no INT NOT NULL COMMENT '鍝嶅簲搴忓彿',
        response_name VARCHAR(200) NOT NULL COMMENT '鍝嶅簲鍚嶇О',
        response_type VARCHAR(32) NOT NULL COMMENT '鍝嶅簲绫诲瀷',
        mode_number INT NULL COMMENT '妯℃€侀樁娆?,
        extra_json JSON NULL COMMENT '鎵╁睍淇℃伅',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '鍒涘缓鏃堕棿',
        PRIMARY KEY (id),
        UNIQUE KEY uk_pid_sol200_response_name (pid, response_name),
        UNIQUE KEY uk_pid_sol200_response_no (pid, response_no),
        KEY idx_pid_sol200_response_type (pid, response_type)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='SOL200 鍝嶅簲閰嶇疆琛?;
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_parameter_definition (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '涓婚敭ID',
        pid BIGINT NOT NULL COMMENT '宸ョ▼ID',
        parameter_name VARCHAR(200) NOT NULL COMMENT '鍙傛暟鍚嶇О',
        expression VARCHAR(500) NULL COMMENT '鍙傛暟琛ㄨ揪寮?,
        scalar_value DOUBLE NULL COMMENT '鍙傛暟鏍囬噺鍊?,
        is_design_parameter TINYINT(1) NOT NULL DEFAULT 0 COMMENT '鏄惁涓鸿璁″弬鏁?,
        design_order INT NULL COMMENT '璁捐鍙傛暟椤哄簭',
        extra_json JSON NULL COMMENT '鎵╁睍淇℃伅',
        PRIMARY KEY (id),
        UNIQUE KEY uk_pid_parameter_definition (pid, parameter_name),
        KEY idx_pid_design_parameter (pid, is_design_parameter, design_order)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='杈撳叆鏂囦欢瑙ｆ瀽鍙傛暟瀹氫箟琛?;
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_parameter_target (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '涓婚敭ID',
        pid BIGINT NOT NULL COMMENT '宸ョ▼ID',
        parameter_name VARCHAR(200) NOT NULL COMMENT '鍙傛暟鍚嶇О',
        target_type VARCHAR(32) NOT NULL COMMENT '鐩爣绫诲瀷',
        set_name VARCHAR(200) NOT NULL COMMENT '闆嗗悎鍚嶇О',
        set_type VARCHAR(32) NOT NULL COMMENT '闆嗗悎绫诲瀷',
        set_scope VARCHAR(32) NOT NULL COMMENT '闆嗗悎鑼冨洿',
        instance_name VARCHAR(200) NULL COMMENT '瀹炰緥鍚嶇О',
        part_name VARCHAR(200) NULL COMMENT '闆朵欢鍚嶇О',
        source_keyword VARCHAR(100) NOT NULL COMMENT '鏉ユ簮鍏抽敭瀛?,
        source_path VARCHAR(255) NOT NULL COMMENT '鏉ユ簮璺緞',
        component_name VARCHAR(100) NULL COMMENT '鍒嗛噺鍚嶇О',
        extra_json JSON NULL COMMENT '鎵╁睍淇℃伅',
        PRIMARY KEY (id),
        KEY idx_pid_parameter_target (pid, parameter_name),
        KEY idx_pid_target_set (pid, set_name, set_type, set_scope)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='杈撳叆鏂囦欢瑙ｆ瀽鍙傛暟鐩爣鏄犲皠琛?;
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_static_sensitivity_response_catalog (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '涓婚敭ID',
        pid BIGINT NOT NULL COMMENT '宸ョ▼ID',
        response_no INT NOT NULL COMMENT '鍝嶅簲搴忓彿',
        request_no INT NOT NULL COMMENT '璇锋眰搴忓彿',
        step_name VARCHAR(200) NULL COMMENT '鍒嗘瀽姝ュ悕绉?,
        frequency INT NOT NULL DEFAULT 1 COMMENT '鍝嶅簲棰戞',
        region_type VARCHAR(32) NOT NULL COMMENT '鍖哄煙绫诲瀷',
        set_name VARCHAR(200) NOT NULL COMMENT '闆嗗悎鍚嶇О',
        set_scope VARCHAR(32) NULL COMMENT '闆嗗悎鑼冨洿',
        instance_name VARCHAR(200) NULL COMMENT '瀹炰緥鍚嶇О',
        part_name VARCHAR(200) NULL COMMENT '闆朵欢鍚嶇О',
        variables_json JSON NULL COMMENT '鍙橀噺淇℃伅',
        extra_json JSON NULL COMMENT '鎵╁睍淇℃伅',
        PRIMARY KEY (id),
        UNIQUE KEY uk_pid_design_response (pid, response_no, request_no),
        KEY idx_pid_design_response_step (pid, step_name)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='杈撳叆鏂囦欢瑙ｆ瀽璁捐鍝嶅簲鐩綍琛?;
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_node_octree_cache (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '涓婚敭',
        pid BIGINT NOT NULL COMMENT '椤圭洰ID',
        source_file_path VARCHAR(500) NOT NULL COMMENT '婧愭枃浠惰矾寰?,
        cache_file_path VARCHAR(500) NOT NULL COMMENT '缂撳瓨鏂囦欢璺緞',
        node_count INT NOT NULL DEFAULT 0 COMMENT '鑺傜偣鏁伴噺',
        instance_count INT NOT NULL DEFAULT 0 COMMENT '瀹炰緥鏁伴噺',
        bbox_min VARCHAR(255) NULL COMMENT '鏁翠綋鍖呭洿鐩掓渶灏忓€?,
        bbox_max VARCHAR(255) NULL COMMENT '鏁翠綋鍖呭洿鐩掓渶澶у€?,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '鏇存柊鏃堕棿',
        PRIMARY KEY (id),
        UNIQUE KEY uk_pid_source_file (pid, source_file_path(255))
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='鑺傜偣鍏弶鏍戠紦瀛樺厓鏁版嵁';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_node_match (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '涓婚敭',
        pid BIGINT NOT NULL COMMENT '椤圭洰ID',
        test_node_id VARCHAR(100) NOT NULL COMMENT '璇曢獙娴嬬偣缂栧彿',
        instance_name VARCHAR(200) NULL COMMENT '瀹炰緥鍚嶇О',
        fem_node_label BIGINT NOT NULL COMMENT '鏈夐檺鍏冭妭鐐瑰彿',
        distance DOUBLE NOT NULL COMMENT '璺濈',
        x_offset DOUBLE NOT NULL COMMENT 'X鍋忕Щ',
        y_offset DOUBLE NOT NULL COMMENT 'Y鍋忕Щ',
        z_offset DOUBLE NOT NULL COMMENT 'Z鍋忕Щ',
        transform_json JSON NULL COMMENT '鍖归厤鏃朵娇鐢ㄧ殑鍙樻崲',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '鍒涘缓鏃堕棿',
        PRIMARY KEY (id),
        UNIQUE KEY uk_pid_test_node (pid, test_node_id),
        KEY idx_pid_instance (pid, instance_name)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='璇曢獙娴嬬偣鍒版湁闄愬厓鑺傜偣鍖归厤缁撴灉';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_dof_match (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '涓婚敭ID',
        pid BIGINT NOT NULL COMMENT '宸ョ▼ID',
        test_node_id VARCHAR(100) NOT NULL COMMENT '璇曢獙娴嬬偣缂栧彿',
        test_dof VARCHAR(32) NOT NULL COMMENT '璇曢獙鑷敱搴?,
        instance_name VARCHAR(200) NULL COMMENT '瀹炰緥鍚嶇О',
        part_name VARCHAR(200) NULL COMMENT '闆朵欢鍚嶇О',
        fem_node_label BIGINT NOT NULL COMMENT '鏈夐檺鍏冭妭鐐瑰彿',
        fem_dof VARCHAR(32) NOT NULL COMMENT '鏈夐檺鍏冭嚜鐢卞害',
        direction_x DOUBLE NOT NULL COMMENT '鏂瑰悜X鍒嗛噺',
        direction_y DOUBLE NOT NULL COMMENT '鏂瑰悜Y鍒嗛噺',
        direction_z DOUBLE NOT NULL COMMENT '鏂瑰悜Z鍒嗛噺',
        match_score DOUBLE NOT NULL DEFAULT 0 COMMENT '鍖归厤寰楀垎',
        transform_json JSON NULL COMMENT '鍙樻崲淇℃伅',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '鍒涘缓鏃堕棿',
        PRIMARY KEY (id),
        UNIQUE KEY uk_pid_test_dof (pid, test_node_id, test_dof),
        KEY idx_pid_fem_node (pid, instance_name, fem_node_label),
        KEY idx_pid_fem_dof (pid, fem_dof)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='鏈夐檺鍏冭嚜鐢卞害鍖归厤缁撴灉琛?;
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_dynamic_response_catalog (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '涓婚敭ID',
        pid BIGINT NOT NULL COMMENT '宸ョ▼ID',
        response_code VARCHAR(200) NOT NULL COMMENT '鍝嶅簲缂栫爜',
        response_name VARCHAR(255) NOT NULL COMMENT '鍝嶅簲鍚嶇О',
        response_type VARCHAR(64) NOT NULL COMMENT '鍝嶅簲绫诲瀷',
        entity_type VARCHAR(64) NOT NULL COMMENT '瀹炰綋绫诲瀷',
        test_mode_no INT NULL COMMENT '璇曢獙鎸瀷鍙?,
        test_node_id VARCHAR(100) NULL COMMENT '璇曢獙娴嬬偣缂栧彿',
        instance_name VARCHAR(200) NULL COMMENT '瀹炰緥鍚嶇О',
        part_name VARCHAR(200) NULL COMMENT '闆朵欢鍚嶇О',
        fem_node_label BIGINT NULL COMMENT '鏈夐檺鍏冭妭鐐瑰彿',
        component VARCHAR(32) NULL COMMENT '鍝嶅簲鍒嗛噺',
        unit VARCHAR(50) NULL COMMENT '鍗曚綅',
        scatter FLOAT NOT NULL DEFAULT 0.05 COMMENT '鍝嶅簲绂绘暎搴?,
        seq_no INT NULL COMMENT '鏄剧ず椤哄簭',
        source_table VARCHAR(100) NULL COMMENT '鏉ユ簮鏁版嵁琛?,
        extra_json JSON NULL COMMENT '鎵╁睍淇℃伅',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '鍒涘缓鏃堕棿',
        PRIMARY KEY (id),
        UNIQUE KEY uk_pid_response_code (pid, response_code),
        KEY idx_pid_response_type (pid, response_type),
        KEY idx_pid_seq (pid, seq_no)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='鏈夐檺鍏冨搷搴旂洰褰曡〃';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_modal_result (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '涓婚敭ID',
        pid BIGINT NOT NULL COMMENT '宸ョ▼ID',
        mode_no INT NOT NULL COMMENT '鏈夐檺鍏冩尟鍨嬪彿',
        frequency DOUBLE NULL COMMENT '棰戠巼鍊?,
        instance_name VARCHAR(200) NULL COMMENT '瀹炰緥鍚嶇О',
        part_name VARCHAR(200) NULL COMMENT '闆朵欢鍚嶇О',
        fem_node_label BIGINT NOT NULL COMMENT '鏈夐檺鍏冭妭鐐瑰彿',
        u1 DOUBLE NULL COMMENT 'X鍚戜綅绉籙1',
        u2 DOUBLE NULL COMMENT 'Y鍚戜綅绉籙2',
        u3 DOUBLE NULL COMMENT 'Z鍚戜綅绉籙3',
        extra_json JSON NULL COMMENT '鎵╁睍淇℃伅',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '鍒涘缓鏃堕棿',
        PRIMARY KEY (id),
        UNIQUE KEY uk_pid_mode_node (pid, mode_no, instance_name, fem_node_label),
        KEY idx_pid_mode (pid, mode_no)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='鏈夐檺鍏冩ā鎬佺粨鏋滆〃';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_static_result (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '涓婚敭ID',
        pid BIGINT NOT NULL COMMENT '宸ョ▼ID',
        load_case_no INT NOT NULL DEFAULT 1 COMMENT '杞借嵎宸ュ喌鍙?,
        instance_name VARCHAR(200) NULL COMMENT '瀹炰緥鍚嶇О',
        part_name VARCHAR(200) NULL COMMENT '闆朵欢鍚嶇О',
        fem_node_label BIGINT NOT NULL COMMENT '鏈夐檺鍏冭妭鐐瑰彿',
        u1 DOUBLE NULL COMMENT 'X鍚戜綅绉籙1',
        u2 DOUBLE NULL COMMENT 'Y鍚戜綅绉籙2',
        u3 DOUBLE NULL COMMENT 'Z鍚戜綅绉籙3',
        ur1 DOUBLE NULL COMMENT 'X鍚戣浆瑙扷R1',
        ur2 DOUBLE NULL COMMENT 'Y鍚戣浆瑙扷R2',
        ur3 DOUBLE NULL COMMENT 'Z鍚戣浆瑙扷R3',
        extra_json JSON NULL COMMENT '鎵╁睍淇℃伅',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '鍒涘缓鏃堕棿',
        PRIMARY KEY (id),
        UNIQUE KEY uk_pid_static_case_node (pid, load_case_no, instance_name, fem_node_label),
        KEY idx_pid_static_case (pid, load_case_no),
        KEY idx_pid_static_node (pid, fem_node_label)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='鏈夐檺鍏冮潤鍔涚粨鏋滆〃';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_modal_correlation (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '涓婚敭ID',
        pid BIGINT NOT NULL COMMENT '宸ョ▼ID',
        test_mode_no INT NOT NULL COMMENT '璇曢獙鎸瀷鍙?,
        fem_mode_no INT NOT NULL COMMENT '鏈夐檺鍏冩尟鍨嬪彿',
        dof_pair_count INT NOT NULL DEFAULT 0 COMMENT '鑷敱搴﹂厤瀵规暟閲?,
        dac DOUBLE NOT NULL COMMENT 'DAC鐧惧垎姣?,
        dsf DOUBLE NOT NULL COMMENT 'DSF鍊?,
        mac DOUBLE NOT NULL COMMENT 'MAC鍊?,
        freq_test DOUBLE NULL COMMENT '璇曢獙棰戠巼',
        freq_fem DOUBLE NULL COMMENT '鏈夐檺鍏冮鐜?,
        freq_error_ratio DOUBLE NULL COMMENT '棰戠巼璇樊姣?,
        flip BOOLEAN NOT NULL DEFAULT FALSE COMMENT '鏄惁闇€瑕佺浉浣嶇炕杞?,
        extra_json JSON NULL COMMENT '鎵╁睍淇℃伅',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '鍒涘缓鏃堕棿',
        PRIMARY KEY (id),
        UNIQUE KEY uk_pid_mode_pair (pid, test_mode_no, fem_mode_no),
        KEY idx_pid_test_mode (pid, test_mode_no),
        KEY idx_pid_fem_mode (pid, fem_mode_no)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='鏈夐檺鍏冩ā鎬佺浉鍏虫€ц〃';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_analysis_run (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '涓婚敭',
        project_id BIGINT NULL COMMENT '椤圭洰ID',
        case_name VARCHAR(200) NULL COMMENT '宸ュ喌鍚嶇О',
        run_no VARCHAR(100) NULL COMMENT '鍒嗘瀽鎵规鍙?,
        source_kind VARCHAR(32) NULL COMMENT '鐏垫晱搴︾粨鏋滄潵婧愮被鍨?,
        op2_path VARCHAR(1024) NULL COMMENT 'OP2缁撴灉鏂囦欢璺緞',
        matrix_path VARCHAR(1024) NULL COMMENT '鐭╅樀缁撴灉鏂囦欢璺緞',
        bdf_path VARCHAR(1024) NULL COMMENT 'BDF鏂囦欢璺緞',
        metadata_path VARCHAR(1024) NULL COMMENT '鍏冩暟鎹枃浠惰矾寰?,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '鍒涘缓鏃堕棿',
        PRIMARY KEY (id),
        KEY idx_created_at (created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='鏈夐檺鍏冨垎鏋愪换鍔¤〃';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_response_def (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '涓婚敭',
        project_id BIGINT NULL COMMENT '椤圭洰ID',
        analysis_run_id BIGINT NOT NULL COMMENT '鍒嗘瀽浠诲姟ID',
        response_code VARCHAR(100) NOT NULL COMMENT '鍝嶅簲缂栫爜',
        response_name VARCHAR(200) NOT NULL COMMENT '鍝嶅簲鍚嶇О',
        response_type VARCHAR(32) NULL COMMENT '鍝嶅簲绫诲瀷',
        mode_number INT NULL COMMENT '妯℃€侀樁娆?,
        unit VARCHAR(50) NULL COMMENT '鍗曚綅',
        seq_no INT NULL COMMENT '鏄剧ず椤哄簭',
        PRIMARY KEY (id),
        UNIQUE KEY uk_run_response_code (analysis_run_id, response_code),
        KEY idx_run_seq (analysis_run_id, seq_no)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='鏈夐檺鍏冨搷搴斿畾涔夎〃';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_parameter_def (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '涓婚敭',
        project_id BIGINT NULL COMMENT '椤圭洰ID',
        analysis_run_id BIGINT NOT NULL COMMENT '鍒嗘瀽浠诲姟ID',
        param_code VARCHAR(100) NOT NULL COMMENT '鍙傛暟缂栫爜',
        param_name VARCHAR(200) NOT NULL COMMENT '鍙傛暟鍚嶇О',
        param_type VARCHAR(32) NULL COMMENT '鍙傛暟绫诲瀷',
        material_id BIGINT NULL COMMENT '鏉愭枡ID',
        property_id BIGINT NULL COMMENT '灞炴€D',
        element_id BIGINT NULL COMMENT '鍗曞厓ID',
        source_material_id BIGINT NULL COMMENT '婧愭潗鏂橧D',
        source_property_id BIGINT NULL COMMENT '婧愬睘鎬D',
        initial_value DOUBLE NULL COMMENT '鍒濆鍊?,
        lower_bound DOUBLE NULL COMMENT '涓嬬晫',
        upper_bound DOUBLE NULL COMMENT '涓婄晫',
        unit VARCHAR(50) NULL COMMENT '鍗曚綅',
        seq_no INT NULL COMMENT '鏄剧ず椤哄簭',
        PRIMARY KEY (id),
        UNIQUE KEY uk_run_param_code (analysis_run_id, param_code),
        KEY idx_run_seq (analysis_run_id, seq_no)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='鏈夐檺鍏冨弬鏁板畾涔夎〃';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_sensitivity_result (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '涓婚敭',
        project_id BIGINT NULL COMMENT '椤圭洰ID',
        analysis_run_id BIGINT NOT NULL COMMENT '鍒嗘瀽浠诲姟ID',
        parameter_id BIGINT NOT NULL COMMENT '鍙傛暟ID',
        response_id BIGINT NOT NULL COMMENT '鍝嶅簲ID',
        sensitivity_value DECIMAL(24,12) NOT NULL COMMENT '鐏垫晱搴﹀€?,
        PRIMARY KEY (id),
        UNIQUE KEY uk_run_param_resp (analysis_run_id, parameter_id, response_id),
        KEY idx_run_param (analysis_run_id, parameter_id),
        KEY idx_run_resp (analysis_run_id, response_id),
        KEY idx_run_resp_value (analysis_run_id, response_id, sensitivity_value)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='鏈夐檺鍏冪伒鏁忓害缁撴灉琛?;
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_analysis_error (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '涓婚敭ID',
        pid BIGINT NOT NULL COMMENT '宸ョ▼ID',
        load_case_no INT NOT NULL DEFAULT 1 COMMENT '杞借嵎宸ュ喌鍙?,
        result_no INT NOT NULL DEFAULT 1 COMMENT '缁撴灉搴忓彿',
        point_no VARCHAR(64) NOT NULL COMMENT '娴嬬偣鍙?,
        node_no VARCHAR(128) NULL COMMENT '鑺傜偣鍙?,
        component_name VARCHAR(32) NOT NULL COMMENT '鍒嗛噺鍚嶇О',
        point_value FLOAT COMMENT '娴嬬偣鍊?,
        initial_node_value FLOAT COMMENT '鍒濆鑺傜偣鍊?,
        initial_relative_error FLOAT COMMENT '鍒濆鐩稿璇樊',
        initial_abs_error REAL COMMENT '鍒濆缁濆璇樊',
        updated_node_value FLOAT COMMENT '淇鍚庤妭鐐瑰€?,
        updated_relative_error FLOAT COMMENT '淇鍚庣浉瀵硅宸?,
        updated_abs_error REAL COMMENT '淇鍚庣粷瀵硅宸?,
        sensor_type_id BIGINT COMMENT '浼犳劅鍣ㄧ被鍨婭D',
        PRIMARY KEY (id),
        UNIQUE KEY uk_pid_case_result_point_component (pid, load_case_no, result_no, point_no, component_name),
        KEY idx_pid (pid),
        KEY idx_pid_point (pid, point_no)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='璇樊鍒嗘瀽琛?;
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_model_update_static_result (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '涓婚敭ID',
        pid BIGINT NOT NULL COMMENT '宸ョ▼ID',
        batch_no VARCHAR(32) NOT NULL COMMENT '鎵规鍙?,
        step_name VARCHAR(200) NULL COMMENT '鍒嗘瀽姝ュ悕绉?,
        frame_idx INT NOT NULL DEFAULT 0 COMMENT '甯у簭鍙?,
        instance_name VARCHAR(200) NULL COMMENT '瀹炰緥鍚嶇О',
        part_name VARCHAR(200) NULL COMMENT '闆朵欢鍚嶇О',
        fem_node_label BIGINT NOT NULL COMMENT '鏈夐檺鍏冭妭鐐瑰彿',
        u1 DOUBLE NULL COMMENT 'X鍚戜綅绉?,
        u2 DOUBLE NULL COMMENT 'Y鍚戜綅绉?,
        u3 DOUBLE NULL COMMENT 'Z鍚戜綅绉?,
        extra_json JSON NULL COMMENT '鎵╁睍淇℃伅',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '鍒涘缓鏃堕棿',
        PRIMARY KEY (id),
        UNIQUE KEY uk_pid_batch_frame_node (pid, batch_no, frame_idx, instance_name, fem_node_label),
        KEY idx_pid_batch (pid, batch_no)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='妯″瀷淇鏈€缁堜綅绉荤粨鏋滆〃';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_model_update_modal_result (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '涓婚敭ID',
        pid BIGINT NOT NULL COMMENT '宸ョ▼ID',
        batch_no VARCHAR(32) NOT NULL COMMENT '鎵规鍙?,
        response_name VARCHAR(200) NOT NULL COMMENT '鍝嶅簲鍚嶇О',
        response_type VARCHAR(32) NOT NULL DEFAULT 'FREQ' COMMENT '鍝嶅簲绫诲瀷',
        fem_mode_no INT NOT NULL COMMENT 'FEM 妯℃€侀樁娆?,
        test_mode_no INT NOT NULL COMMENT '璇曢獙妯℃€侀樁娆?,
        freq_fem_initial DOUBLE NULL COMMENT '淇鍓?FEM 棰戠巼',
        freq_fem_updated DOUBLE NULL COMMENT '淇鍚?FEM 棰戠巼',
        freq_test DOUBLE NULL COMMENT '鐩爣璇曢獙棰戠巼',
        initial_relative_error DOUBLE NULL COMMENT '淇鍓嶇浉瀵硅宸?%)',
        updated_relative_error DOUBLE NULL COMMENT '淇鍚庣浉瀵硅宸?%)',
        mac DOUBLE NULL COMMENT '鍖归厤浣跨敤鐨?MAC',
        extra_json JSON NULL COMMENT '鎵╁睍淇℃伅',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '鍒涘缓鏃堕棿',
        PRIMARY KEY (id),
        UNIQUE KEY uk_pid_batch_modal_pair (pid, batch_no, fem_mode_no, test_mode_no),
        KEY idx_pid_batch (pid, batch_no)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='妯″瀷淇鏈€缁堟ā鎬侀鐜囩粨鏋滆〃';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_dac_dsf (
        pid BIGINT NOT NULL COMMENT '宸ョ▼ID',
        dac FLOAT COMMENT 'DAC鎸囨爣鍊?,
        dsf FLOAT COMMENT 'DSF鎸囨爣鍊?,
        PRIMARY KEY (pid)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='妯℃€佺浉鍏虫€ф寚鏍囪〃';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_tracking_value (
        pid BIGINT NOT NULL COMMENT '宸ョ▼ID',
        batch_no INT NOT NULL DEFAULT 1 COMMENT '鎵规鍙?,
        tracking_type VARCHAR(10) NOT NULL COMMENT '璺熻釜绫诲瀷锛堝弬鏁拌窡韪?鍝嶅簲璺熻釜锛?,
        tracking_name VARCHAR(100) NOT NULL COMMENT '璺熻釜鍚嶇О',
        iteration INT NOT NULL COMMENT '杩唬姝?,
        track_value FLOAT COMMENT '璺熻釜鍊?,
        PRIMARY KEY (pid, batch_no, tracking_type, tracking_name, iteration)
    ) COMMENT='璺熻釜鍊艰〃';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_tracking_iteration (
        pid BIGINT NOT NULL COMMENT '宸ョ▼ID',
        batch_no INT NOT NULL DEFAULT 1 COMMENT '鎵规鍙?,
        iterations INT COMMENT '杩唬娆℃暟',
        PRIMARY KEY (pid, batch_no)
    ) COMMENT='璺熻釜杩唬琛?;
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_bayesian_iteration_metric (
        project_id BIGINT NOT NULL COMMENT 'project id',
        batch_no INT NOT NULL DEFAULT 1 COMMENT 'batch number',
        iteration INT NOT NULL COMMENT 'iteration number',
        ccabs DOUBLE COMMENT 'CCABS convergence metric',
        rel_res DOUBLE COMMENT 'relative residual norm',
        ra_norm DOUBLE COMMENT 'model response norm',
        re_norm DOUBLE COMMENT 'target response norm',
        dr_norm DOUBLE COMMENT 'response residual norm',
        dx_norm DOUBLE COMMENT 'parameter update norm',
        max_abs_dparam DOUBLE COMMENT 'maximum absolute parameter update',
        mean_abs_response_diff DOUBLE COMMENT 'mean absolute response difference percent',
        max_abs_response_diff DOUBLE COMMENT 'maximum absolute response difference percent',
        PRIMARY KEY (project_id, batch_no, iteration)
    ) COMMENT='bayesian iteration metrics';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_relevance_tracking (
        pid BIGINT NOT NULL COMMENT '宸ョ▼ID',
        iteration INT NOT NULL COMMENT '杩唬姝?,
        type VARCHAR(32) NOT NULL COMMENT '鎸囨爣绫诲瀷',
        value FLOAT COMMENT '鎸囨爣鍊?,
        PRIMARY KEY (pid, iteration, type)
    ) COMMENT='妯″瀷淇鐩稿叧鎬ф寚鏍囪窡韪〃';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_parameter_variation (
        pid BIGINT NOT NULL COMMENT '宸ョ▼ID',
        batch_no INT NOT NULL DEFAULT 1 COMMENT '鎵规鍙?,
        parameter_name VARCHAR(100) NOT NULL COMMENT '鍙傛暟鍚嶇О',
        parameter_hierarchy VARCHAR(10) COMMENT '鍙傛暟灞傜骇',
        parameter_type VARCHAR(10) COMMENT '鍙傛暟绫诲瀷',
        parameter_scope VARCHAR(32) COMMENT '鍙傛暟鑼冨洿',
        ori_value FLOAT COMMENT '鍘熷鍊?,
        result_value FLOAT COMMENT '缁撴灉鍊?,
        parameter_variation FLOAT COMMENT '鍙傛暟鍙樺寲',
        PRIMARY KEY (pid, batch_no, parameter_name)
    ) COMMENT='鍙傛暟鍙樺寲琛?;
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_response_difference (
        pid INTEGER NOT NULL COMMENT '宸ョ▼ID',
        batch_no INT NOT NULL DEFAULT 1 COMMENT '鎵规鍙?,
        response_name VARCHAR(100) NOT NULL COMMENT '鍝嶅簲鍚嶇О',
        iteration INT NOT NULL COMMENT '杩唬娆℃暟',
        cal_result_value FLOAT COMMENT '璁＄畻缁撴灉鍊?,
        test_result_value FLOAT COMMENT '娴嬭瘯缁撴灉鍊?,
        response_diff FLOAT COMMENT '鍝嶅簲宸紓(%)',
        PRIMARY KEY (pid, batch_no, response_name, iteration)
    ) COMMENT='鍝嶅簲宸紓琛?;
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_measuring_point_info(
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '涓婚敭ID',
        measuring_point_name VARCHAR(64) NOT NULL COMMENT '娴嬬偣鍚嶇О',
        project_id BIGINT NOT NULL COMMENT '椤圭洰ID',
        sensor_type_id BIGINT NOT NULL COMMENT '浼犳劅鍣ㄧ被鍨婭D',
        x_position DOUBLE NOT NULL COMMENT 'X鍧愭爣',
        y_position DOUBLE NOT NULL COMMENT 'Y鍧愭爣',
        z_position DOUBLE NOT NULL COMMENT 'Z鍧愭爣',
        x_angle DOUBLE NOT NULL COMMENT '瑙掑害x',
        y_angle DOUBLE NOT NULL COMMENT '瑙掑害y',
        z_angle DOUBLE NOT NULL COMMENT '瑙掑害z',
        data_source VARCHAR(32) NOT NULL COMMENT '鏁版嵁鏉ユ簮',
        PRIMARY KEY (id, measuring_point_name)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='娴嬬偣淇℃伅琛?; 
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_channel_info (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '涓婚敭ID',
        channel_name VARCHAR(64) NOT NULL COMMENT '閫氶亾鍚嶇О',
        measure_point_id BIGINT NOT NULL COMMENT '娴嬬偣ID',
        project_id BIGINT NOT NULL COMMENT '椤圭洰ID',
        direction INT NOT NULL COMMENT '鏂瑰悜:1-x, 2-y, 3-z',
        data_operate CHAR(1) NOT NULL COMMENT '+ - 鎿嶄綔绫诲瀷',
        PRIMARY KEY (id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='閫氶亾淇℃伅琛?;
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_background_task (
        task_id VARCHAR(64) NOT NULL COMMENT '浠诲姟ID',
        task_type VARCHAR(128) NOT NULL COMMENT '浠诲姟绫诲瀷',
        interface_code VARCHAR(128) NULL COMMENT '鎺ュ彛缂栫爜',
        task_kind VARCHAR(32) NOT NULL DEFAULT 'internal' COMMENT '浠诲姟鍒嗙被',
        project_id BIGINT NULL COMMENT '椤圭洰ID',
        status VARCHAR(32) NOT NULL COMMENT '浠诲姟鐘舵€?,
        execute_count INT NOT NULL DEFAULT 0 COMMENT '宸叉墽琛屾鏁?,
        max_execute_count INT NOT NULL DEFAULT 3 COMMENT '鏈€澶ф墽琛屾鏁?,
        handler_module VARCHAR(255) NULL COMMENT '澶勭悊鍑芥暟妯″潡',
        handler_name VARCHAR(128) NULL COMMENT '澶勭悊鍑芥暟鍚嶇О',
        pass_task_id TINYINT(1) NOT NULL DEFAULT 0 COMMENT '鏄惁浼犻€抰ask_id',
        submitted_at VARCHAR(64) NULL COMMENT '鎻愪氦鏃堕棿',
        started_at VARCHAR(64) NULL COMMENT '寮€濮嬫椂闂?,
        finished_at VARCHAR(64) NULL COMMENT '缁撴潫鏃堕棿',
        request_json LONGTEXT NULL COMMENT '璇锋眰JSON',
        kwargs_json LONGTEXT NULL COMMENT '鎵ц鍙傛暟JSON',
        progress_json LONGTEXT NULL COMMENT '杩涘害JSON',
        result_json LONGTEXT NULL COMMENT '缁撴灉JSON',
        error_json LONGTEXT NULL COMMENT '閿欒JSON',
        PRIMARY KEY (task_id),
        KEY idx_task_status (status),
        KEY idx_project_interface (project_id, interface_code),
        KEY idx_project_status (project_id, status)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='妯″瀷淇鍚庡彴浠诲姟琛?;
    """
]

OBSOLETE_TABLE_NAMES = (
    "t_mt_py_fem_response_overview",
    "t_mt_py_fem_responses",
    "t_mt_py_fem_displacement_responses",
    "t_mt_py_fem_strain_responses",
    "t_mt_py_fem_stress_responses",
    "t_mt_py_fem_relative_error",
    "t_mt_py_fem_confidence",
    "t_mt_py_fem_displacement_scale_factor",
    "t_mt_py_fem_correlation_scatter",
    "t_mt_py_fem_manual_response",
)

_tables_ensured = False
_tables_ensure_lock = threading.Lock()
_connection_pool = None
_connection_pool_lock = threading.Lock()


def _connection_kwargs():
    return {
        "host": DB_CONFIG["host"],
        "port": DB_CONFIG["port"],
        "user": DB_CONFIG["user"],
        "password": DB_CONFIG["password"],
        "database": DB_CONFIG["database"],
        "charset": DB_CONFIG["charset"],
        "use_pure": True,
    }


def _get_connection_pool():
    global _connection_pool
    if _connection_pool is not None:
        return _connection_pool

    _connection_pool_lock.acquire()
    try:
        if _connection_pool is None:
            pool_name = str(DB_CONFIG.get("pool_name", "virtualreal702_pool"))
            pool_size = max(1, int(DB_CONFIG.get("pool_size", 10)))
            pool_reset_session = bool(DB_CONFIG.get("pool_reset_session", True))
            _connection_pool = mysql.connector.pooling.MySQLConnectionPool(
                pool_name=pool_name,
                pool_size=pool_size,
                pool_reset_session=pool_reset_session,
                **_connection_kwargs(),
            )
        return _connection_pool
    finally:
        if _connection_pool_lock.locked():
            _connection_pool_lock.release()

def get_connection():
    return _get_connection_pool().get_connection()


def initialize_database_runtime(*, ensure_tables: bool = True, warm_connection: bool = True) -> None:
    """
    Eagerly create the MySQL connection pool during application startup.

    Why this exists:
    - The old lazy-init path built the pool on the first business request.
    - That makes the first few frontend calls look "stuck", even though the
      actual delay is pool creation / initial database handshake.
    - Moving this work to startup makes request latency more stable and also
      fails fast when the database configuration is invalid.
    """
    _get_connection_pool()
    if warm_connection:
        conn = get_connection()
        try:
            conn.ping(reconnect=True, attempts=1, delay=0)
        finally:
            conn.close()
    if ensure_tables:
        ensure_tables_exist()


def ensure_tables_exist():
    global _tables_ensured
    if _tables_ensured:
        return
    _tables_ensure_lock.acquire()
    if _tables_ensured:
        _tables_ensure_lock.release()
        return
    conn = get_connection()
    cursor = conn.cursor()
    try:
        def _drop_table_if_exists(table_name):
            cursor.execute(f"DROP TABLE IF EXISTS {table_name}")

        def _rename_table_if_needed(old_name, new_name):
            cursor.execute(
                """
                SELECT TABLE_NAME
                FROM information_schema.TABLES
                WHERE TABLE_SCHEMA = %s
                  AND TABLE_NAME IN (%s, %s)
                """,
                (DB_CONFIG["database"], old_name, new_name),
            )
            existing_names = {str(row[0]) for row in (cursor.fetchall() or [])}
            if old_name in existing_names and new_name not in existing_names:
                cursor.execute(f"RENAME TABLE {old_name} TO {new_name}")

        def _ensure_column(table_name, column_name, column_sql):
            cursor.execute(
                """
                SELECT 1
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = %s
                  AND TABLE_NAME = %s
                  AND COLUMN_NAME = %s
                LIMIT 1
                """,
                (DB_CONFIG["database"], table_name, column_name),
            )
            if cursor.fetchone() is None:
                cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_sql}")

        _rename_table_if_needed(
            "t_mt_py_fem_design_response_catalog",
            "t_mt_py_fem_static_sensitivity_response_catalog",
        )
        _rename_table_if_needed(
            "t_mt_py_fem_response_catalog",
            "t_mt_py_fem_dynamic_response_catalog",
        )
        for table_name in OBSOLETE_TABLE_NAMES:
            _drop_table_if_exists(table_name)

        for sql in CREATE_TABLE_SQL_LIST:
            cursor.execute(sql)
        _ensure_column(
            "t_mt_py_background_task",
            "interface_code",
            "interface_code VARCHAR(128) NULL COMMENT '鎺ュ彛缂栫爜'",
        )
        _ensure_column(
            "t_mt_py_background_task",
            "task_kind",
            "task_kind VARCHAR(32) NOT NULL DEFAULT 'internal' COMMENT '浠诲姟鍒嗙被'",
        )
        _ensure_column(
            "t_mt_py_background_task",
            "project_id",
            "project_id BIGINT NULL COMMENT '椤圭洰ID'",
        )
        _ensure_column(
            "t_mt_py_background_task",
            "execute_count",
            "execute_count INT NOT NULL DEFAULT 0 COMMENT '宸叉墽琛屾鏁?",
        )
        _ensure_column(
            "t_mt_py_background_task",
            "max_execute_count",
            "max_execute_count INT NOT NULL DEFAULT 3 COMMENT '鏈€澶ф墽琛屾鏁?",
        )
        _ensure_column(
            "t_mt_py_background_task",
            "handler_module",
            "handler_module VARCHAR(255) NULL COMMENT '澶勭悊鍑芥暟妯″潡'",
        )
        _ensure_column(
            "t_mt_py_background_task",
            "handler_name",
            "handler_name VARCHAR(128) NULL COMMENT '澶勭悊鍑芥暟鍚嶇О'",
        )
        _ensure_column(
            "t_mt_py_background_task",
            "pass_task_id",
            "pass_task_id TINYINT(1) NOT NULL DEFAULT 0 COMMENT '鏄惁浼犻€抰ask_id'",
        )
        _ensure_column(
            "t_mt_py_background_task",
            "request_json",
            "request_json LONGTEXT NULL COMMENT '璇锋眰JSON'",
        )
        _ensure_column(
            "t_mt_py_background_task",
            "kwargs_json",
            "kwargs_json LONGTEXT NULL COMMENT '鎵ц鍙傛暟JSON'",
        )
        _ensure_column(
            "t_mt_py_background_task",
            "progress_json",
            "progress_json LONGTEXT NULL COMMENT '杩涘害JSON'",
        )
        _ensure_column(
            "t_mt_py_background_task",
            "result_json",
            "result_json LONGTEXT NULL COMMENT '缁撴灉JSON'",
        )
        _ensure_column(
            "t_mt_py_background_task",
            "error_json",
            "error_json LONGTEXT NULL COMMENT '閿欒JSON'",
        )
        _ensure_column(
            "t_mt_py_fem_selected_parameter",
            "usage_scope",
            "usage_scope JSON NULL COMMENT 'parameter usage scope'",
        )
        _ensure_column(
            "t_mt_py_fem_dynamic_response_catalog",
            "enabled",
            "enabled TINYINT(1) NOT NULL DEFAULT 1 COMMENT 'response enabled flag'",
        )
        _ensure_column(
            "t_mt_py_fem_dynamic_response_catalog",
            "selection_source",
            "selection_source VARCHAR(64) NULL COMMENT 'response selection source'",
        )
        _ensure_column(
            "t_mt_py_fem_dynamic_response_catalog",
            "solver_scope",
            "solver_scope JSON NULL COMMENT 'response solver scope'",
        )
        _ensure_column(
            "t_mt_py_fem_dynamic_response_catalog",
            "scatter",
            "scatter FLOAT NOT NULL DEFAULT 0.05 COMMENT 'response scatter'",
        )
        _ensure_column(
            "t_mt_py_fem_dynamic_response_catalog",
            "updated_at",
            "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT 'updated time'",
        )
        _ensure_column(
            "t_mt_py_fem_quantity_set_capability",
            "is_internal",
            "is_internal TINYINT(1) NOT NULL DEFAULT 0 COMMENT '鏄惁涓篈baqus鑷姩鐢熸垚鐨勫唴閮ㄩ泦鍚?_PickedSetNN)锛?=鍐呴儴 0=鐢ㄦ埛'",
        )
        cursor.execute(
            """
            SELECT 1
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = %s
              AND TABLE_NAME = %s
              AND COLUMN_NAME = %s
            LIMIT 1
            """,
            (DB_CONFIG["database"], "t_mt_py_fem_shell_property", "element_set"),
        )
        if cursor.fetchone() is None:
            cursor.execute(
                """
                ALTER TABLE t_mt_py_fem_shell_property
                ADD COLUMN element_set VARCHAR(255) NULL COMMENT '鍗曞厓闆嗗悕绉?
                """
            )
        _ensure_column(
            "t_mt_py_fem_modal_correlation",
            "flip",
            "flip BOOLEAN NOT NULL DEFAULT FALSE COMMENT '鏄惁闇€瑕佺浉浣嶇炕杞?",
        )
        _ensure_column(
            "t_mt_py_fem_analysis_run",
            "source_kind",
            "source_kind VARCHAR(32) NULL COMMENT '鐏垫晱搴︾粨鏋滄潵婧愮被鍨?",
        )
        _ensure_column(
            "t_mt_py_fem_analysis_run",
            "op2_path",
            "op2_path VARCHAR(1024) NULL COMMENT 'OP2缁撴灉鏂囦欢璺緞'",
        )
        _ensure_column(
            "t_mt_py_fem_analysis_run",
            "matrix_path",
            "matrix_path VARCHAR(1024) NULL COMMENT '鐭╅樀缁撴灉鏂囦欢璺緞'",
        )
        _ensure_column(
            "t_mt_py_fem_analysis_run",
            "bdf_path",
            "bdf_path VARCHAR(1024) NULL COMMENT 'BDF鏂囦欢璺緞'",
        )
        _ensure_column(
            "t_mt_py_fem_analysis_run",
            "metadata_path",
            "metadata_path VARCHAR(1024) NULL COMMENT '鍏冩暟鎹枃浠惰矾寰?",
        )
        _ensure_column(
            "t_mt_py_fem_response_def",
            "response_type",
            "response_type VARCHAR(32) NULL COMMENT '鍝嶅簲绫诲瀷'",
        )
        _ensure_column(
            "t_mt_py_fem_response_def",
            "mode_number",
            "mode_number INT NULL COMMENT '妯℃€侀樁娆?",
        )
        _ensure_column(
            "t_mt_py_fem_parameter_def",
            "param_type",
            "param_type VARCHAR(32) NULL COMMENT '鍙傛暟绫诲瀷'",
        )
        _ensure_column(
            "t_mt_py_fem_parameter_def",
            "material_id",
            "material_id BIGINT NULL COMMENT '鏉愭枡ID'",
        )
        _ensure_column(
            "t_mt_py_fem_parameter_def",
            "property_id",
            "property_id BIGINT NULL COMMENT '灞炴€D'",
        )
        _ensure_column(
            "t_mt_py_fem_parameter_def",
            "element_id",
            "element_id BIGINT NULL COMMENT '鍗曞厓ID'",
        )
        _ensure_column(
            "t_mt_py_fem_parameter_def",
            "source_material_id",
            "source_material_id BIGINT NULL COMMENT '婧愭潗鏂橧D'",
        )
        _ensure_column(
            "t_mt_py_fem_parameter_def",
            "source_property_id",
            "source_property_id BIGINT NULL COMMENT '婧愬睘鎬D'",
        )
        _ensure_column(
            "t_mt_py_fem_parameter_def",
            "initial_value",
            "initial_value DOUBLE NULL COMMENT '鍒濆鍊?",
        )
        _ensure_column(
            "t_mt_py_fem_parameter_def",
            "lower_bound",
            "lower_bound DOUBLE NULL COMMENT '涓嬬晫'",
        )
        _ensure_column(
            "t_mt_py_fem_parameter_def",
            "upper_bound",
            "upper_bound DOUBLE NULL COMMENT '涓婄晫'",
        )
        _ensure_column(
            "t_mt_py_fem_static_sensitivity_response_catalog",
            "set_scope",
            "set_scope VARCHAR(32) NULL COMMENT '闆嗗悎鑼冨洿'",
        )
        _ensure_column(
            "t_mt_py_fem_static_sensitivity_response_catalog",
            "instance_name",
            "instance_name VARCHAR(200) NULL COMMENT '瀹炰緥鍚嶇О'",
        )
        _ensure_column(
            "t_mt_py_fem_static_sensitivity_response_catalog",
            "part_name",
            "part_name VARCHAR(200) NULL COMMENT '闆朵欢鍚嶇О'",
        )
        cursor.execute(
            """
            SELECT 1
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = %s
              AND TABLE_NAME = %s
              AND COLUMN_NAME = %s
            LIMIT 1
            """,
            (DB_CONFIG["database"], "t_mt_py_fem_layered_property", "Offset"),
        )
        layered_offset_exists = cursor.fetchone() is not None
        cursor.execute(
            """
            SELECT 1
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = %s
              AND TABLE_NAME = %s
              AND COLUMN_NAME = %s
            LIMIT 1
            """,
            (DB_CONFIG["database"], "t_mt_py_fem_layered_property", "Offset_L"),
        )
        layered_offset_l_exists = cursor.fetchone() is not None
        if layered_offset_exists and not layered_offset_l_exists:
            cursor.execute(
                """
                ALTER TABLE t_mt_py_fem_layered_property
                CHANGE COLUMN `Offset` Offset_L DOUBLE NULL COMMENT 'Offset'
                """
            )
        conn.commit()
        _tables_ensured = True
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
        if _tables_ensure_lock.locked():
            _tables_ensure_lock.release()


def clear_unv_tables(cursor, pid):
    cursor.execute(f"DELETE FROM t_mt_py_test_node WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_test_element WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_test_modal_frequency WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_test_modal_shape WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_test_modal_shape_imag WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_test_modal_shape_real WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_test_static_result WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_node_pairs WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_dof_pairs WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_node_match WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_dof_match WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_dynamic_response_catalog WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_modal_correlation WHERE pid = {pid}")


def clear_fem_tables(cursor, pid):
    cursor.execute(f"DELETE FROM t_mt_py_fem_coord WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_material_overview WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_isotropic WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_ortho2d WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_aniso3d WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_property WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_shell_property WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_beam_property WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_solid_property WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_layered_property WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_boundary WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_node_pairs WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_transform_operation WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_dof_pairs WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_static_shape_pairs WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_quantity_set_capability WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_selected_parameter WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_sol200_parameter_config WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_parameter_definition WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_parameter_target WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_static_sensitivity_response_catalog WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_sol200_response_config WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_node_octree_cache WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_node_match WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_dof_match WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_dynamic_response_catalog WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_modal_result WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_static_result WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_modal_correlation WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_response_difference WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_parameter_variation WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_tracking_iteration WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_tracking_value WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_bayesian_iteration_metric WHERE project_id = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_relevance_tracking WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_analysis_error WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_model_update_static_result WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_model_update_modal_result WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_dac_dsf WHERE pid = {pid}")
