import com.fasterxml.jackson.databind.ObjectMapper;
import com.jh.knowledgeos.config.WorkloadRequestFilter;
import com.jh.knowledgeos.domain.ApiKey;
import com.jh.knowledgeos.exception.WorkloadProofException;
import com.jh.knowledgeos.mapper.WorkloadIdentityRepository;
import com.jh.knowledgeos.service.AgentKeyProofVerifier;
import java.nio.file.*;
import java.time.Instant;
import java.util.*;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.mock.web.MockHttpServletRequest;

/** Runs the actual AKB verifier against a proof produced by this repository. */
public class AkbVerifierProbe {
  @SuppressWarnings("unchecked")
  public static void main(String[] args) throws Exception {
    ObjectMapper json=new ObjectMapper();Map<String,Object> fixture=json.readValue(Files.readString(Path.of(args[0])),Map.class);
    Map<String,Object> jwk=(Map<String,Object>)fixture.get("publicJwk"),binding=(Map<String,Object>)fixture.get("binding");
    String uuid=(String)binding.get("externalAgentId"),kid=(String)jwk.get("kid"),token=(String)fixture.get("proof");
    byte[] body=((String)fixture.get("body")).getBytes(java.nio.charset.StandardCharsets.UTF_8);
    Map<String,Object> registered=new HashMap<>();registered.put("id",31L);registered.put("status","ACTIVE");registered.put("public_jwk",json.writeValueAsString(jwk));
    Set<String> consumed=new HashSet<>();
    WorkloadIdentityRepository repository=new WorkloadIdentityRepository(new JdbcTemplate()) {
      public Map<String,Object> binding(long policy,String external,String runtime) {return policy==17&&uuid.equals(external)&&uuid.equals(runtime)?Map.of("id",21L,"agent_principal_id",4L):null;}
      public Map<String,Object> agentKey(long policy,long id,String key) {return policy==17&&id==21&&kid.equals(key)?registered:null;}
      public void consumeV2(long policy,String key,String jti,Instant expires) {if(!consumed.add(jti))throw new DuplicateKeyException("replay");}
    };
    Map<String,Object> policy=new HashMap<>();
    for(String[] pair:new String[][]{{"iss","issuer"},{"aud","audience"},{"resource","resource_origin"},{"platformId","platform_id"},{"installationId","installation_id"},{"workspaceId","workspace_id"}}) {
      String input=pair[0].equals("iss")?"issuer":pair[0].equals("aud")?"audience":pair[0];policy.put(pair[1],binding.get(input));
    }
    policy.put("authorization_mode","SERVICE_SCOPE");
    ApiKey key=new ApiKey();key.setId(1L);key.setPrincipalId(123L);key.setPrincipalType("APPLICATION");key.setWorkloadPolicyId(17L);key.setIdentityMode("APPLICATION_WORKLOAD");
    AgentKeyProofVerifier verifier=new AgentKeyProofVerifier(repository);verifier.setPublicPathPrefix("/gateway");
    if(!uuid.equals(verifier.authenticate(request(fixture,token,body),key,policy).getExternalAgentId()))throw new AssertionError("identity");
    reject(()->verifier.authenticate(request(fixture,token,body),key,policy));
    reject(()->verifier.authenticate(request(fixture,token,"tampered".getBytes()),key,policy));
    MockHttpServletRequest wrong=request(fixture,token,body);wrong.removeHeader("X-API-Key");wrong.addHeader("X-API-Key","wrong");reject(()->verifier.authenticate(wrong,key,policy));
    registered.put("status","REVOKED");reject(()->verifier.authenticate(request(fixture,token,body),key,policy));
    System.out.println("PASS: actual AKB Java verifier accepts SDK V2 proof with Unicode body and gateway prefix; rejects replay, body/credential tampering and revoked keys");
  }
  private static MockHttpServletRequest request(Map<String,Object> f,String token,byte[] body) {
    MockHttpServletRequest r=new MockHttpServletRequest("POST","/api/mcp");r.addHeader("X-API-Key",f.get("serviceKey"));r.addHeader(WorkloadRequestFilter.HEADER,token);r.setAttribute(WorkloadRequestFilter.BODY,body);return r;
  }
  private static void reject(Runnable call){try{call.run();throw new AssertionError("accepted invalid proof");}catch(WorkloadProofException expected){}}
}
